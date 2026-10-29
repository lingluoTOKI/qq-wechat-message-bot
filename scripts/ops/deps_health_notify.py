#!/usr/bin/env python3
"""依赖健康体检 + 主动告警

背景：edge-tts / networkx 等手装包若未持久化，容器重建后会静默失效
      （曾导致语音 100% 失效）。本脚本每天检查一次，缺失时**私聊主动通知**。

设计要点
--------
* 完全脱离 bot 进程：自己换 access_token 并调 QQ 官方 C2C 接口，
  bot 容器重启 / 插件失效都不影响告警能力。
* 状态去重：同一问题只告警一次，恢复后再通知一次（不刷屏）。
* 凭据不落盘：secret 与 openid 均从现有配置/绑定表读取。
"""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

CMD_CFG = os.environ.get("DEPS_CFG_PATH",
                       "/opt/bot-stack/astrbot/data/cmd_config.json")
STATE_DB = "/opt/bot-stack/astrbot/data/plugin_data/wushu_ops/assistant.sqlite"
STATE_FILE = "/opt/bot-stack/logs/deps-health-state.json"
LOG_FILE = "/opt/bot-stack/logs/deps-health.log"
PLATFORM_ID = "大肥鱼-官方"

# 配置守卫：AstrBot 面板保存配置是全量覆盖，会把「显示工具调用」这类
# 不常碰的开关误写成 True —— 表现是工具调用内容直接发进聊天框。
# 框架默认值是 False，所以只要发现为 True 就是被误开了。
GUARD_SWITCHES = ("show_tool_call_result", "show_tool_use_status")

# 需要体检的 import 名 → (用途, 所属插件)
CHECKS = [
    ("edge_tts", "语音合成", "clonetts"),
    ("networkx", "图记忆", "livingmemory"),
    ("pytz", "时区", "livingmemory"),
    ("cv2", "图像识别", "group_guardian"),
    ("tomli", "配置解析", "proactive_chat"),
    ("apscheduler", "定时调度", "proactive_chat"),
    ("jieba", "中文分词", "livingmemory"),
    ("faiss", "向量检索", "meme_manager"),
    ("boto3", "R2 图床", "meme_manager"),
    ("ahocorasick", "AC 词库", "group_guardian"),
]


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_imports():
    """在容器内逐个 import，返回缺失列表。"""
    missing = []
    for mod, usage, plugin in CHECKS:
        r = subprocess.run(
            ["docker", "exec", "astrbot", "python", "-c", "import %s" % mod],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            missing.append((mod, usage, plugin))
    # 自测开关：DEPS_HEALTH_FAKE_FAIL=1 时注入一条假缺失，用于演练告警链路
    # （不必真去动生产依赖文件）
    if os.environ.get("DEPS_HEALTH_FAKE_FAIL") == "1":
        missing.append(("fake-test-module", "演练用", "self-test"))
    return missing


def get_credentials():
    d = json.load(open(CMD_CFG, encoding="utf-8"))
    appid = secret = ""
    for p in d.get("platform", []):
        if p.get("type") == "qq_official" and p.get("enable", True):
            appid = p.get("appid", "")
            secret = p.get("secret", "")
            break
    if not (appid and secret):
        return None, None
    return appid, secret


def get_admin_openid():
    """从 wushu_ops 已绑定的管理员通知里取 openid。

    注意：state 表里存的是 JSON 字符串（含引号），必须 json.loads 解出来再切，
    否则 openid 末尾会带一个 " 导致 QQ 返回 40011036 user_id无效。
    """
    import sqlite3
    try:
        c = sqlite3.connect("file:%s?mode=ro" % STATE_DB, uri=True, timeout=5)
        row = c.execute("select v from state where k='admin'").fetchone()
        c.close()
        if not row:
            return None
        raw = row[0]
        if isinstance(raw, str) and raw.startswith('"'):
            raw = json.loads(raw)
        parts = raw.split(":", 2)
        if len(parts) != 3 or not parts[2]:
            return None
        return parts[2]
    except Exception as e:
        log("读取 admin 绑定失败: %s" % type(e).__name__)
        return None


def get_access_token(appid, secret):
    req = urllib.request.Request(
        "https://bots.qq.com/app/getAppAccessToken",
        data=json.dumps({"appId": appid, "clientSecret": secret}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode())
    return j.get("access_token")


def send_c2c(openid, token, text):
    url = "https://api.sgroup.qq.com/v2/users/%s/messages" % openid
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": text, "msg_type": 0}).encode(),
        headers={"Authorization": "QQBot %s" % token,
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode())
    return bool(j.get("id"))


def read_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"failed": [], "notified": False}


def write_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def guard_config():
    """配置守卫：检测被误开的「显示工具调用」开关并自动关回。

    AstrBot 面板保存配置是全量覆盖，不常碰的开关可能被写成 True，
    表现是 LLM 的工具调用内容直接发进聊天框。框架默认值是 False，
    所以只要发现为 True 即可判定为误开。
    返回被修正的开关名列表（空=一切正常）。
    """
    try:
        raw = open(CMD_CFG, "rb").read()
        has_bom = raw[:3] == b"\xef\xbb\xbf"
        with open(CMD_CFG, encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception as e:
        log("配置守卫读取失败: %s: %s" % (type(e).__name__, str(e)[:80]))
        return []
    ps = d.get("provider_settings")
    if not isinstance(ps, dict):
        return []
    bad = [k for k in GUARD_SWITCHES if ps.get(k)]
    if not bad:
        return []
    for k in bad:
        ps[k] = False
    try:
        with open(CMD_CFG, "w", encoding="utf-8-sig" if has_bom else "utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("配置守卫写回失败: %s: %s" % (type(e).__name__, str(e)[:80]))
        return []
    log("配置守卫：已关闭被误开的开关 %s" % ", ".join(bad))
    return bad


def notify(text):
    """私聊 TOKI；失败只记日志，不抛异常。"""
    appid, secret = get_credentials()
    if not appid:
        log("缺少 QQ 官方平台凭据，无法告警")
        return False
    openid = get_admin_openid()
    if not openid:
        log("未找到已绑定的管理员通知目标（先在私聊执行 /绑定管理员通知）")
        return False
    try:
        token = get_access_token(appid, secret)
        ok = send_c2c(openid, token, text)
        log("告警已发送: %s" % ("成功" if ok else "接口未返回回执"))
        return ok
    except Exception as e:
        log("告警发送失败: %s: %s" % (type(e).__name__, str(e)[:120]))
        return False


def main():
    log("===== 依赖体检开始 =====")
    # 配置守卫：面板误开「显示工具调用」会导致工具内容发进聊天框
    guarded = guard_config()
    if guarded:
        notify("【配置守卫】检测到面板误开了开关：%s\n"
               "已自动关闭（工具调用内容不会再发进聊天框）。\n"
               "配置改动需重启容器才生效，请安排一次重启。"
               % "、".join(guarded))
    missing = check_imports()
    st = read_state()
    prev_missing = st.get("failed", [])
    prev_notified = st.get("notified", False)

    if missing:
        names = ", ".join(m[0] for m in missing)
        log("缺失 %d 个依赖: %s" % (len(missing), names))
        if not prev_notified:
            lines = ["【依赖体检告警】大肥鱼有 %d 个依赖包丢失：" % len(missing)]
            for mod, usage, plugin in missing:
                lines.append("· %s（%s，插件 %s）" % (mod, usage, plugin))
            lines.append("")
            lines.append("影响：相关功能可能已静默失效（如 edge_tts 丢失＝语音发不出去）。")
            lines.append("修复：pip install --target /AstrBot/data/pylibs --no-deps %s"
                         % " ".join(m[0] for m in missing))
            delivered = notify("\n".join(lines))
            # 只有真的送达才标记，避免一次网络抖动就永久不再告警
            st["notified"] = delivered
            if not delivered:
                log("告警未送达，下次体检将重试")
        else:
            log("已在告警状态，不重复通知")
    else:
        log("依赖全部正常")
        if prev_notified:
            notify("【依赖体检】依赖已全部恢复（%d 个包重新可用），无需处理。"
                   % len(prev_missing))
            st["notified"] = False

    st["failed"] = [m[0] for m in missing]
    st["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_state(st)
    log("===== 体检结束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())

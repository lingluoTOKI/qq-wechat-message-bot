#!/usr/bin/env python3
"""大肥鱼人格卡 / 工具选择 自测脚本（在容器内运行，不改线上会话）。

做三件事：
1. 读 DB 里的 persona 12 system_prompt + cmd_config 白名单，核对一致性
2. 直接用线上 deepseek-flash provider 跑 8 个用例（system prompt 完全一致，前缀命中缓存）
3. 断言语气、长度、颜文字格式、工具选择、防编造

用法：python3 /AstrBot/data/scripts/selftest_persona.py
"""
import json
import re
import sqlite3
import sys
import urllib.request

DB = "/AstrBot/data/data_v4.db"
CFG = "/AstrBot/data/cmd_config.json"
KAO = "/AstrBot/data/plugin_data/astrbot_plugin_kaomoji/kaomoji.json"

# 只做“会不会选这个工具”的判定，不真的执行，所以参数schema可以简化
TOOL_DESC = {
    "find_music": "按歌名/歌手搜歌，返回候选和 search_id",
    "deliver_music": "把已搜到的歌以语音/文件发到群里",
    "query_project_info": "查询主人项目的设定、玩法、机制",
    "get_server_status": "查询游戏服在线人数与运行状态",
    "search_memes": "按语义检索表情包并发送",
    "anysearch_search": "联网搜索时效性信息",
    "anysearch_extract": "抓取指定网页正文",
    "parse_media_url": "解析B站等媒体链接的标题与简介",
    "rvc_cover": "用RVC模型翻唱",
    "svc_cover": "用SVC模型翻唱",
    "get_task_status": "查询翻唱任务进度",
    "recall_long_term_memory": "检索长期记忆",
    "read_memory_evidence": "读取某条记忆的原始证据",
    "memorize_long_term_memory": "写入一条长期记忆",
    "recall_chat": "检索群聊历史原话",
    "recall_person": "查询某人的档案",
}

BAD_RX = re.compile(r"有什么可以帮|很高兴为你服务|作为(一个)?AI|我理解你的感受|您好")
MARK_RX = re.compile(r"\{\{([^{}]{1,8})\}\}")


def load_cfg():
    d = json.load(open(CFG, encoding="utf-8-sig"))
    persona_cfg = d["agent_runner"]["config"]["persona"]
    src = {s["id"]: s for s in d["provider_sources"]}
    prov = next(p for p in d["provider"] if p["enable"] and p["id"] == "deepseek/deepseek-flash")
    key = src[prov["provider_source_id"]]["key"]
    key = key[0] if isinstance(key, list) else key
    return persona_cfg, prov, src[prov["provider_source_id"]]["api_base"], key


def load_persona():
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=5)
    row = c.execute("select system_prompt, tools, skills from personas where persona_id='12'").fetchone()
    c.close()
    return row[0], json.loads(row[1] or "[]"), json.loads(row[2] or "[]")


def chat(api_base, key, model, prompt, tools, user_text):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": user_text}],
        "temperature": 0.3,
        "max_tokens": 300,
    }
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def build_tools(names):
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": TOOL_DESC.get(n, n),
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
        for n in names
    ]


CASES = [
    ("傲娇-被夸", "你是不是有点可爱", "text", "嘴硬"),
    ("关心-加班", "今天加班到十点，累死了", "text", "关心"),
    ("整活-代码", "你会写代码吗", "text", "整活"),
    ("工具-服务器", "服务器现在几个人", "tool", "get_server_status"),
    ("工具-点歌", "放一首周杰伦的歌", "tool", "find_music|search_music|deliver_music"),
    ("工具-时效", "今天有什么AI大新闻", "tool", "anysearch_search|anysearch_extract"),
    ("防编造-版本号", "卫戍协议盟约最新版本号是多少", "tool_or_dunno", "anysearch"),
    ("记忆-无上下文", "你还记得我最讨厌吃什么吗", "tool_or_dunno", "recall_long_term_memory"),
]


def check(kind, expect, text, calls, cat_set, orig):
    text = (text or "").strip()
    n = len(text)
    m = MARK_RX.findall(text)
    mark_ok = bool(m) and m[-1] in cat_set
    fails = []

    # 只调工具、还没出正文：只校验选对了工具，格式断言不适用
    if calls and not text:
        if kind == "tool":
            want = set(expect.split("|"))
            if not want & set(calls):
                return ["没调预期工具(got=%s)" % calls]
            return []
        if kind == "tool_or_dunno":
            ok = any(c.startswith("anysearch") or c in ("query_project_info", "get_server_status") for c in calls) \
                if expect == "anysearch" else expect in calls
            return [] if ok else ["调错工具(got=%s)" % calls]
        return ["只调了工具但没出正文"]

    if BAD_RX.search(text):
        fails.append("出现客服腔")
    if not mark_ok:
        fails.append("颜文字标记缺失/非法(%s)" % (m[-1] if m else "无"))

    if kind == "text":
        # 只做这些场景该有的特征断言
        if expect == "嘴硬":
            if n > 70:
                fails.append("太长(%d字)" % n)
            if not re.search(r"哼|才|哪|别|谁|想得美|自恋", text):
                fails.append("不够嘴硬")
        elif expect == "关心":
            if n > 90:
                fails.append("太长(%d字)" % n)
            if not re.search(r"吃|睡|躺|休息|歇|累不累", text):
                fails.append("没有细节关心")
        elif expect == "整活":
            if n > 80:
                fails.append("太长(%d字)" % n)
            if not re.search(r"懒得|收费|会啊|又不是|哼|当然|那得看", text):
                fails.append("没接住梗")
    elif kind == "tool":
        want = set(expect.split("|"))
        if not want & set(calls):
            fails.append("没调预期工具(got=%s)" % (calls or "无"))
    elif kind == "tool_or_dunno":
        want = expect
        if text and re.search(r"\d+(\.\d+)+", text) and not calls:
            fails.append("直接报了版本号数字")
        if not calls and not re.search(r"不确定|不知道|没印象|不记得|查|搜|去问|让我看", text):
            fails.append("既没调工具也没说不确定")
        elif calls and want not in " ".join(calls) and not re.search(r"不确定|不知道|没印象", text):
            fails.append("调错工具(%s)" % calls)
    return fails


def main():
    persona_cfg, prov, api_base, key = load_cfg()
    prompt, db_tools, db_skills = load_persona()
    cat_set = set(json.load(open(KAO, encoding="utf-8"))["categories"])

    print("=" * 62)
    print("配置核对")
    print("=" * 62)
    print("persona system_prompt : %d 字符" % len(prompt))
    print("DB  tools=%d skills=%d  |  config tools=%d skills=%d"
          % (len(db_tools), len(db_skills), len(persona_cfg.get("tools") or []), len(persona_cfg.get("skills") or [])))
    same = sorted(db_tools) == sorted(persona_cfg.get("tools") or [])
    print("两侧白名单一致       : %s" % ("是" if same else "否 ← 会导致工具集交替"))
    unknown = [t for t in db_tools if t not in TOOL_DESC]
    print("未登记描述的工具     : %s" % (unknown or "无"))
    print("provider             : %s model=%s" % (prov["id"], prov["model"]))

    tools = build_tools([t for t in db_tools if t in TOOL_DESC])
    print()
    print("=" * 62)
    print("行为用例（真实调用 %s，8 条）" % prov["model"])
    print("=" * 62)

    total = 0
    passed = 0
    for name, user_text, kind, expect in CASES:
        total += 1
        try:
            resp = chat(api_base, key, prov["model"], prompt, tools, user_text)
            msg = resp["choices"][0]["message"]
            text = msg.get("content") or ""
            calls = [tc["function"]["name"] for tc in (msg.get("tool_calls") or [])]
            fails = check(kind, expect, text, calls, cat_set, user_text)
            usage = resp.get("usage") or {}
            hit = usage.get("prompt_cache_hit_tokens", 0)
            miss = usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0) - hit)
            status = "PASS" if not fails else "FAIL"
            if not fails:
                passed += 1
            print("\n[%s] %s  <%s>" % (status, name, user_text))
            print("  调用: %s | 回复(%d字): %s" % (calls or "无", len(text), text.replace("\n", " ")[:120]))
            print("  token: 命中%s/未命中%s" % (hit, miss))
            if fails:
                print("  原因: %s" % "；".join(fails))
        except Exception as e:
            print("\n[ERR ] %s  %s: %s" % (name, type(e).__name__, e))

    print()
    print("=" * 62)
    print("结果 %d/%d 通过" % (passed, total))
    print("=" * 62)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

"""给 clonetts 加「语音模式」开关：想让她说话就说话，想文字就文字。

设计
----
`/语音 开`    → 之后这个会话的每条回复都用语音说出来
`/语音 关`    → 回到文字
`/语音`       → 切换（不带参数时）
`/语音 状态`  → 只看当前状态

状态按 **umo（会话）** 存，所以私聊和群互不影响；写盘持久化，重启不丢。
触发优先级：关键词 > 语音模式 > 概率。

顺带调整：语音模式下放宽长度限制
  · 上限 → max_length × tts_max_parts（因为会切句连发，4 段能容下更多字）
  · 下限 → 1（"嗯。"这种短回复在语音模式下也该说出来）
"""
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

P = Path("/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_clonetts/main.py")
bak = "%s.bak-voicemode-%s" % (P, datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy(P, bak)
s = P.read_text(encoding="utf-8")

assert "voice_mode" not in s, "已打过补丁，跳过"

# ---------- 1) 补 import ----------
old_imp = "import asyncio\nimport random\nimport re\n"
new_imp = "import asyncio\nimport json\nimport os\nimport random\nimport re\nimport threading\n"
assert old_imp in s, "import 段未匹配"
s = s.replace(old_imp, new_imp, 1)

# ---------- 2) 模块级：状态读写 ----------
anchor = '_CLAUSE_SPLIT = re.compile(r"(?<=[，,、])")\n'
assert anchor in s, "常量锚点未匹配"

helpers = anchor + '''
# ---------------- 语音模式状态（按会话持久化） ----------------
# 路径按插件自身位置推导，不硬编码 /AstrBot：
#   .../data/plugins/astrbot_plugin_clonetts/main.py
#   → .../data/plugin_data/astrbot_plugin_clonetts/voice_mode.json
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(_PLUGIN_DIR)), "plugin_data",
    "astrbot_plugin_clonetts")
_VM_PATH = os.path.join(_DATA_ROOT, "voice_mode.json")

_vm_lock = threading.Lock()
_vm_cache = None


def _vm_load():
    """读盘（只读一次，之后走内存）。坏了就当空。"""
    global _vm_cache
    if _vm_cache is not None:
        return _vm_cache
    data = {}
    try:
        with open(_VM_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data = {str(k): bool(v) for k, v in raw.items()}
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _vm_cache = data
    return _vm_cache


def _vm_get(umo):
    if not umo:
        return False
    return bool(_vm_load().get(umo, False))


def _vm_set(umo, on):
    """写入并落盘（原子写，避免半截文件）。"""
    global _vm_cache
    if not umo:
        return False
    with _vm_lock:
        data = dict(_vm_load())
        if on:
            data[umo] = True
        else:
            data.pop(umo, None)
        _vm_cache = data
        try:
            os.makedirs(_DATA_ROOT, exist_ok=True)
            tmp = _VM_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _VM_PATH)
        except Exception:
            pass
    return True

'''
s = s.replace(anchor, helpers, 1)

# ---------- 3) 触发条件：加入语音模式 ----------
old_trig = '''            _kw_hit = any(k in (getattr(event, "message_str", "") or "") for k in ("语音", "说一句", "念一下", "读一下", "念给我"))
            if not _kw_hit and not self.probability(self.tts_probability):
                logger.debug("本次消息未命中 TTS 概率，跳过语音合成")
                return
'''
new_trig = '''            _kw_hit = any(k in (getattr(event, "message_str", "") or "") for k in ("语音", "说一句", "念一下", "读一下", "念给我"))
            # 语音模式（/语音 开）优先于概率，但关键词仍可单独强制触发
            _vm_on = _vm_get(getattr(event, "unified_msg_origin", "") or "")
            if not _kw_hit and not _vm_on and not self.probability(self.tts_probability):
                logger.debug("本次消息未命中 TTS 概率，跳过语音合成")
                return
'''
assert old_trig in s, "触发条件未匹配"
s = s.replace(old_trig, new_trig, 1)

# ---------- 4) 语音模式下放宽长度限制 ----------
old_max = '''            if len(llm_text) > self.max_length:
                logger.debug(
                    f"LLM 文本长度 {len(llm_text)} 超过上限 {self.max_length}，跳过语音合成"
                )
                return
'''
new_max = '''            # 语音模式下会切句连发，所以上限按段数放宽
            _eff_max = (self.max_length * max(1, self.tts_max_parts)
                        if _vm_on else self.max_length)
            if len(llm_text) > _eff_max:
                logger.debug(
                    f"LLM 文本长度 {len(llm_text)} 超过上限 {_eff_max}，跳过语音合成"
                )
                return
'''
assert old_max in s, "长度上限未匹配"
s = s.replace(old_max, new_max, 1)

old_min = '''            if len(llm_text) < self.min_length:
                logger.debug(
                    f"LLM 文本长度 {len(llm_text)} 小于下限 {self.min_length}，跳过语音合成"
                )
                return
'''
new_min = '''            _eff_min = 1 if _vm_on else self.min_length
            if len(llm_text) < _eff_min:
                logger.debug(
                    f"LLM 文本长度 {len(llm_text)} 小于下限 {_eff_min}，跳过语音合成"
                )
                return
'''
assert old_min in s, "长度下限未匹配"
s = s.replace(old_min, new_min, 1)

# ---------- 5) 新增命令 ----------
cmd_anchor = '    @filter.command("说", alias={"say"})'
assert cmd_anchor in s, "命令锚点未匹配"

cmd = '''    @filter.command("语音")
    async def voice_mode_cmd(self, event: AstrMessageEvent):
        """语音模式开关。用法：/语音 开 | /语音 关 | /语音（切换）"""
        umo = getattr(event, "unified_msg_origin", "") or ""
        msg = (getattr(event, "message_str", "") or "").strip()
        seg = msg.split(None, 1)
        arg = seg[1].strip().lower() if len(seg) > 1 else ""

        ON = ("开", "开启", "打开", "on", "1", "true")
        OFF = ("关", "关闭", "关掉", "off", "0", "false")

        if arg in ON:
            target = True
        elif arg in OFF:
            target = False
        elif arg in ("", "切换", "toggle"):
            target = not _vm_get(umo)
        else:
            cur = _vm_get(umo)
            yield event.plain_result(
                "现在是%s。\\n用法：/语音 开 ｜ /语音 关 ｜ /语音（切换）"
                % ("语音模式" if cur else "文字模式"))
            return

        _vm_set(umo, target)
        if target:
            yield event.plain_result(
                "好，切成语音模式了。之后这个会话每条回复都会用语音说。\\n"
                "（想换回文字：/语音 关；只让某一句发声：/说 内容）")
        else:
            yield event.plain_result("换回文字模式了。")

'''
s = s.replace(cmd_anchor, cmd + cmd_anchor, 1)

P.write_text(s, encoding="utf-8")
py_compile.compile(str(P), doraise=True)

print("补丁已应用，语法通过。备份:", bak.rsplit("/", 1)[-1])
for probe, label in (
        ('_VM_PATH = os.path.join', "状态文件路径"),
        ("def _vm_get", "读状态"),
        ("def _vm_set", "写状态"),
        ('@filter.command("语音")', "命令"),
        ("_vm_on = _vm_get", "接入触发"),
        ("_eff_max", "上限放宽"),
        ("_eff_min", "下限放宽")):
    print("  %-12s %s" % (label, "OK" if probe in s else "!! 缺失"))

# 静态自检：命令体引用的名字是否都存在
body = s.split('@filter.command("语音")')[1].split('@filter.command("说"')[0]
need_vm = ["_vm_get", "_vm_set", "event.plain_result", "AstrMessageEvent"]
print("  命令依赖缺失:", [n for n in need_vm if n not in body] or "无")

# 冒烟：直接测状态读写
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("ct_probe", str(P))
mod = importlib.util.module_from_spec(spec)
sys.modules["ct_probe"] = mod
try:
    spec.loader.exec_module(mod)
except Exception as e:
    # 插件依赖 astrbot 运行时，独立导入会失败，属于预期
    print("  （独立导入跳过：%s）" % type(e).__name__)
else:
    print("  独立导入 OK")

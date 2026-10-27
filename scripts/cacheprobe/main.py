"""缓存诊断探针（临时插件）。

目的：定位「同一 umo 下交替出现有人格 / 无人格请求」的真正调用方。

做法：在 ``on_llm_request`` 最晚阶段记录每次请求的指纹，并回溯调用栈，
找出 astrbot 内部是哪个模块发起的（例如 astr_agent_tool_exec / proactive_chat /
meme_manager / astr_main_agent 的行号）。

输出前缀统一为 ``CACHEPROBE``，便于 grep。诊断完即可禁用/删除本插件。
"""

import traceback

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PREFIX = "CACHEPROBE"

# 这些栈帧是"中间人"，真正想知道的是它们外面的入口
_INTERESTING = (
    "astr_agent_tool_exec",
    "proactive_chat",
    "meme_manager",
    "livingmemory",
    "group_guardian",
    "clonetts",
    "astr_main_agent",
    "cron/manager",
    "astr_main_agent_resources",
)


def _caller_trace():
    """回溯调用栈，返回触发本次 LLM 请求的上游模块链。

    之前用白名单过滤，结果经常匹配不到（caller=?），改成：取所有 astrbot 内部
    或被加载插件里的栈帧，从外到内保留最外层 4 个，这样能直接看出入口是谁。
    本探针自身的栈帧要排除。
    """
    try:
        stack = traceback.extract_stack()
    except Exception:
        return "?"
    hits = []
    for fr in stack:
        fn = (fr.filename or "").replace("\\", "/")
        if "cacheprobe" in fn:
            continue
        if "/astrbot/" not in fn and "/plugins/" not in fn:
            continue
        parts = fn.split("/")
        base = parts[-1]
        parent = parts[-2] if len(parts) >= 2 else ""
        hits.append("%s/%s:%d" % (parent, base, fr.lineno))
    if not hits:
        return "?"
    # 最外层（入口）在前
    return " <- ".join(hits[:4])


@register("cacheprobe", "诊断", "缓存破坏探针（临时）", "1.0.0")
class CacheProbe(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self._seen = {}

    @filter.on_llm_request(priority=1000)
    async def probe(self, event: AstrMessageEvent, req):
        try:
            sp = req.system_prompt or ""
            umo = ""
            try:
                umo = event.unified_msg_origin or ""
            except Exception:
                pass
            has_persona = "# Persona Instructions" in sp
            has_skills = "## Skills" in sp
            has_default_chat = "你正在浏览聊天软件" in sp
            has_safe = "You are running in Safe Mode" in sp
            try:
                tcount = len(req.func_tool.tools) if req.func_tool else 0
            except Exception:
                tcount = -1
            msg_len = 0
            try:
                msg_len = len(req.prompt or "")
            except Exception:
                pass

            kind = "PERSONA" if has_persona else "NOPERSONA"
            logger.info(
                "%s kind=%s umo=%s sp_len=%d skills=%s default_chat=%s "
                "safe=%s tools=%d prompt_len=%d caller=%s",
                PREFIX, kind, umo[-24:], len(sp), has_skills, has_default_chat,
                has_safe, tcount, msg_len, _caller_trace(),
            )
        except Exception as e:
            logger.warning("%s 探针自身异常: %s: %s", PREFIX, type(e).__name__, e)

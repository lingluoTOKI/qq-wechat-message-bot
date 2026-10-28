"""真实版 /ctxbudget：只讲真话，不让模型编诊断。

背景：
  群里有人反复发 /ctxbudget，而这个命令原本**并不存在** → 命令落到 LLM →
  模型照着历史里旧格式"演"一段诊断（编造 hash、编造缓存占比），每次烧 2~3k
  输入 token，还把群里的人带偏。

这里做三件事：
  1. 注册真的 /ctxbudget（管理员）：返回**从 provider_stats 与本插件捕获的真实数据**。
  2. 非管理员发 /ctxbudget（或不带斜杠的 ctxbudget）→ **静默忽略**，0 token、不回复。
  3. 未注册的斜杠命令 → 非管理员静默忽略；管理员回一句"未知命令"（同样 0 token）。
     目的：不让任何"打错/不存在的命令"落进 LLM 白烧钱。

数据来源（都不经过模型）：
  · 本插件的 on_llm_request 钩子：捕获当轮 system / 工具 / 历史 / 临时注入的构成与哈希
  · data_v4.db 的 provider_stats：DeepSeek 返回的真实 input/cached/output
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter

DATA_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATS_DB = os.path.join(DATA_DIR, "data_v4.db")

# 命令名（不含斜杠）与别名，用于判定"这是不是一条命令"
CMD_CTXBUDGET = "ctxbudget"


def _est_tokens(s: str) -> int:
    """粗略估算：ASCII 按 4 字符 1 token，其余按 1 字符 1 token。"""
    if not s:
        return 0
    ascii_n = sum(1 for ch in s if ord(ch) < 128)
    return (ascii_n + 3) // 4 + (len(s) - ascii_n)


def _hash(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()[:8]


def _strip_noise(text: str) -> str:
    """去掉引用块、At 标记、图片占位，只留用户自己打的字。"""
    t = text or ""
    out = []
    depth = 0
    skip_tag = None
    for ch in t:
        if ch == "<":
            skip_tag = ">" if depth == 0 else skip_tag
            depth += 1
            continue
        if ch == ">" and depth > 0:
            depth -= 1
            if depth == 0:
                skip_tag = None
            continue
        if depth == 0:
            out.append(ch)
    t = "".join(out)
    t = t.replace("[图片]", " ").replace("[表情]", " ").replace("[语音]", " ")
    return t.strip()


def _first_token(text: str) -> str:
    t = _strip_noise(text)
    if not t:
        return ""
    tok = t.split()[0]
    return tok.lstrip("/").strip().lower()


_KNOWN_CACHE: set[str] | None = None


def _known_commands() -> set[str]:
    """从框架的命令注册表里取所有已注册命令名与别名（带缓存，首次打印便于核对）。"""
    global _KNOWN_CACHE
    if _KNOWN_CACHE is not None:
        return _KNOWN_CACHE
    names: set[str] = set()
    try:
        from astrbot.core.star.register.star_handler import star_handlers_registry
        from astrbot.core.star.filter.command import CommandFilter

        handlers = list(getattr(star_handlers_registry, "_handlers", []) or [])
        if not handlers:
            handlers = list(getattr(star_handlers_registry, "star_handlers_map", {}).values())
        for h in handlers:
            for f in getattr(h, "event_filters", []) or []:
                if not isinstance(f, CommandFilter):
                    continue
                if f.command_name:
                    names.add(str(f.command_name).lstrip("/").lower())
                for a in (getattr(f, "alias", None) or set()):
                    names.add(str(a).lstrip("/").lower())
                for p in (getattr(f, "parent_command_names", None) or []):
                    if p:
                        names.add(str(p).lstrip("/").lower())
    except Exception as e:  # 取不到就当"全都认识"，绝不误伤
        logger.warning("[ctxbudget] 取命令注册表失败，放行全部命令：%s" % str(e)[:120])
        return {"__all__"}
    try:
        logger.info("[ctxbudget] 已注册命令 %d 个：%s" % (
            len(names), "、".join(sorted(names))[:600]))
    except Exception:
        pass
    _KNOWN_CACHE = names
    return names


class CtxBudgetMixin:
    """真实诊断 + 未知命令守门人。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cb_sessions: dict[str, dict[str, Any]] = {}

    # ---------- 捕获当轮请求构成 ----------

    @filter.on_llm_request(priority=200000)
    async def ctxbudget_capture(self, event: AstrMessageEvent, req):
        """记录 system/工具/历史/临时注入的构成与哈希（只存哈希与长度，不存原文）。"""
        try:
            if not event:
                return
            key = str(getattr(event, "unified_msg_origin", "") or "unknown")
            tools = []
            try:
                ft = getattr(req, "func_tool", None)
                tools = list(getattr(ft, "tools", []) or []) if ft else []
            except Exception:
                tools = []
            tool_chars = 0
            tool_names = []
            for t in tools:
                try:
                    tool_names.append(str(getattr(t, "name", "")))
                    tool_chars += len(json.dumps(
                        getattr(t, "parameters", None) or {}, ensure_ascii=False, default=str))
                    tool_chars += len(str(getattr(t, "description", "") or ""))
                except Exception:
                    continue
            contexts = list(getattr(req, "contexts", []) or [])
            ctx_chars = 0
            ctx_roles = []
            for c in contexts:
                if isinstance(c, dict):
                    ctx_roles.append(str(c.get("role", "?")))
                    ctx_chars += len(json.dumps(c.get("content"), ensure_ascii=False, default=str))
                else:
                    ctx_roles.append(str(getattr(c, "role", "?")))
                    ctx_chars += len(str(getattr(c, "content", "") or ""))
            extra = list(getattr(req, "extra_user_content_parts", []) or [])
            extra_chars = 0
            for p in extra:
                try:
                    extra_chars += len(str(getattr(p, "text", "") or ""))
                except Exception:
                    pass
            system = getattr(req, "system_prompt", "") or ""
            prev = self._cb_sessions.get(key, {})
            self._cb_sessions[key] = {
                "ts": time.time(),
                "system_chars": len(system),
                "system_hash": _hash(system),
                "system_changed": bool(prev) and prev.get("system_hash") != _hash(system),
                "prompt_chars": len(getattr(req, "prompt", "") or ""),
                "tool_count": len(tools),
                "tool_names": tool_names,
                "tool_chars": tool_chars,
                "tools_changed": bool(prev) and prev.get("tool_names") != tool_names,
                "ctx_count": len(contexts),
                "ctx_chars": ctx_chars,
                "ctx_roles": ctx_roles,
                "extra_count": len(extra),
                "extra_chars": extra_chars,
            }
        except Exception as e:
            logger.debug("[ctxbudget] 捕获失败：%s" % str(e)[:120])

    # ---------- 守门人：未知命令 / 非管理员命令一律不进 LLM ----------

    @filter.on_llm_request(priority=200001)
    async def ctxbudget_gate(self, event: AstrMessageEvent, req):
        try:
            raw = str(getattr(event, "message_str", "") or "")
            clean = _strip_noise(raw)
            if not clean:
                return
            is_slash = clean.startswith("/")
            tok = _first_token(clean)
            if not tok:
                return
            is_admin = False
            try:
                is_admin = bool(event.is_admin())
            except Exception:
                is_admin = False

            # 我们的诊断命令：任何形式都自己处理，绝不让模型"演"
            if tok == CMD_CTXBUDGET:
                event.stop_event()
                if is_admin:
                    await self._send_ctxbudget(event)
                return

            # 未注册的斜杠命令：非管理员静默，管理员给提示（都是 0 token）
            if is_slash:
                known = _known_commands()
                if "__all__" in known or tok in known:
                    return
                event.stop_event()
                if is_admin:
                    await event.send(event.plain_result(
                        f"未知命令 /{tok}。发 /功能 看有哪些命令可用。"))
        except Exception as e:
            logger.debug("[ctxbudget] 守门失败：%s" % str(e)[:120])

    # ---------- 真实数据 ----------

    def _recent_usage(self) -> dict[str, Any]:
        """从 provider_stats 取最近一条 DeepSeek 的真实用量。"""
        try:
            conn = sqlite3.connect("file:" + STATS_DB + "?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT start_time, provider_id, token_input_other, token_input_cached, "
                    "token_output FROM provider_stats WHERE provider_id LIKE 'deepseek%' "
                    "ORDER BY start_time DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return {}
            st, pid, miss, hit, out = row
            return {
                "time": st,
                "total": (miss or 0) + (hit or 0),
                "hit": hit or 0,
                "miss": miss or 0,
                "out": out or 0,
                "rate": (hit or 0) * 100.0 / ((miss or 0) + (hit or 0)) if ((miss or 0) + (hit or 0)) else 0,
            }
        except Exception as e:
            logger.debug("[ctxbudget] 读 provider_stats 失败：%s" % str(e)[:120])
            return {}

    def _session_stats(self, event: AstrMessageEvent) -> dict[str, Any]:
        key = str(getattr(event, "unified_msg_origin", "") or "unknown")
        return self._cb_sessions.get(key, {})

    async def _send_ctxbudget(self, event: AstrMessageEvent):
        s = self._session_stats(event)
        u = self._recent_usage()
        if not s and not u:
            await event.send(event.plain_result(
                "还没有可统计的请求——先聊一句再来。"))
            return
        lines = ["[本群上下文诊断 · 真实数据]"]
        if s:
            lines.append(
                "本轮构成：system %d 字符(≈%d tok) | 工具 %d 个 / 定义 %d 字符 | "
                "历史 %d 条 / %d 字符(≈%d tok) | 临时注入 %d 段 / %d 字符(≈%d tok)"
                % (
                    s.get("system_chars", 0), _est_tokens("x" * s.get("system_chars", 0)),
                    s.get("tool_count", 0), s.get("tool_chars", 0),
                    s.get("ctx_count", 0), s.get("ctx_chars", 0),
                    _est_tokens("x" * s.get("ctx_chars", 0)),
                    s.get("extra_count", 0), s.get("extra_chars", 0),
                    _est_tokens("x" * s.get("extra_chars", 0)),
                )
            )
            lines.append(
                "前缀稳定性：system %s | 工具 %s"
                % ("变了" if s.get("system_changed") else "未变",
                   "变了" if s.get("tools_changed") else "未变")
            )
        if u:
            lines.append(
                "DeepSeek 最近一次：输入 %d（缓存 %d / 未缓存 %d）输出 %d，缓存占比 %.1f%%"
                % (u["total"], u["hit"], u["miss"], u["out"], u["rate"])
            )
        lines.append("（以上为框架与 provider_stats 的真实读数，非模型估算）")
        await event.send(event.plain_result("\n".join(lines)))

    # ---------- 注册命令 ----------

    @filter.command("ctxbudget")
    async def ctxbudget_cmd(self, event: AstrMessageEvent):
        """管理员用的上下文/缓存真实诊断。"""
        try:
            if not event.is_admin():
                return  # 非管理员：静默忽略，不回复、不耗 token
        except Exception:
            return
        await self._send_ctxbudget(event)

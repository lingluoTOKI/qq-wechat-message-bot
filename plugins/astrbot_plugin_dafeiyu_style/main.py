"""大肥鱼表达风格：只学「怎么说」，不碰记忆、不碰回复决策、不改人格。"""
import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

try:
    from dafeiyu_runtime.paid import ModelBudgetBlocked, guarded_model_call
    from dafeiyu_runtime.session_budget import scope
except Exception:  # 运行时未安装时降级为不走预算门（仍可用）
    ModelBudgetBlocked = type("ModelBudgetBlocked", (RuntimeError,), {})
    guarded_model_call = None
    scope = None

try:
    from . import config as _config
    from . import inject as _inject
    from . import learning as _learning
    from .store import Store, record_budget, record_zhipu_budget
    from . import zhipu
except ImportError:  # pragma: no cover
    import config as _config
    import inject as _inject
    import learning as _learning
    from store import Store, record_budget, record_zhipu_budget
    import zhipu

PLUGIN_NAME = "dafeiyu_style"
MEMORY_PLUGIN_DIR = "astrbot_plugin_dafeiyu_daily_memory"
TICK_SECONDS = 90


def _extract_text(resp) -> str:
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    for attr in ("completion_text", "text", "content"):
        value = getattr(resp, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(resp, dict):
        for key in ("completion_text", "text", "content"):
            value = resp.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return str(resp)


async def llm_ask(context, provider_id: str, system_prompt: str, prompt: str,
                  timeout: float = 90.0, max_tokens: int = 1200, on_response=None) -> str:
    """一次纯文本问答；失败抛异常，由调用方决定是否放弃本轮。"""

    async def _run():
        if hasattr(context, "llm_generate"):
            try:
                resp = await context.llm_generate(
                    chat_provider_id=provider_id, prompt=prompt,
                    system_prompt=system_prompt, max_tokens=max_tokens, request_max_retries=1,
                )
                if on_response is not None:
                    await asyncio.to_thread(on_response, resp)
                text = _extract_text(resp)
                if text.strip():
                    return text
            except TypeError:
                raise
        provider = None
        if hasattr(context, "get_provider_by_id"):
            provider = context.get_provider_by_id(provider_id)
        if provider is not None and hasattr(provider, "text_chat"):
            try:
                resp = await provider.text_chat(
                    system_prompt=system_prompt, prompt=prompt, max_tokens=max_tokens)
                if on_response is not None:
                    await asyncio.to_thread(on_response, resp)
            except TypeError:
                raise
            text = _extract_text(resp)
            if text.strip():
                return text
        raise RuntimeError("Provider %s 不可用" % provider_id)

    return await asyncio.wait_for(_run(), timeout=timeout)


@register("astrbot_plugin_dafeiyu_style", "TOKI",
          "大肥鱼表达风格：只读情景记忆归档，提炼群内表达/黑话/问答示例，人工审批后临时注入；走预算门，不改人格",
          "0.1.5")
class StylePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.data_dir = str(StarTools.get_data_dir(PLUGIN_NAME))
        self.config_path = os.path.join(self.data_dir, "config.json")
        os.makedirs(self.data_dir, exist_ok=True)
        self.cfg = _config.load_config(self.config_path)
        self._ensure_config()
        self.store = Store(os.path.join(self.data_dir, "style.db"))
        self.memory_db = self._resolve_memory_db()
        self._cfg_mtime = 0.0
        self._task = None
        self._learning_lock = asyncio.Lock()

    # ---------- 基础 ----------

    def _ensure_config(self):
        """首次安装时落一份默认配置，方便直接改。"""
        if os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(_config.DEFAULTS, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[dafeiyu_style] 默认配置写入失败：%s" % str(exc)[:120])

    def _resolve_memory_db(self) -> str:
        override = str(self.cfg.get("memory_db_path", "") or "").strip()
        if override:
            return override
        parent = os.path.dirname(self.data_dir)
        for name in (MEMORY_PLUGIN_DIR, "dafeiyu_daily_memory"):
            candidate = os.path.join(parent, name, "memory.db")
            if os.path.exists(candidate):
                return candidate
        return os.path.join(parent, MEMORY_PLUGIN_DIR, "memory.db")

    def _reload_cfg(self):
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return
        if mtime > self._cfg_mtime:
            self.cfg = _config.load_config(self.config_path)
            self.memory_db = self._resolve_memory_db()
            self._cfg_mtime = mtime

    def _group_allowed(self, group_id: str) -> bool:
        groups = self.cfg.get("groups") or []
        return not groups or str(group_id) in {str(g) for g in groups}

    def _event_group(self, event) -> str:
        if not event.get_group_id():
            return ''
        origin = str(getattr(event, 'unified_msg_origin', '') or '')
        return origin if ':GroupMessage:' in origin else ''

    async def initialize(self):
        self._cfg_mtime = 0.0
        self._reload_cfg()
        if self.cfg.get('auto_review',True):
            groups=await asyncio.to_thread(_learning.read_groups,self.memory_db)
            for group in groups:
                if self._group_allowed(group):
                    result=await _learning.review_existing(self.memory_db,group,self.store)
                    if result['approved'] or result['rejected']:
                        logger.info('[dafeiyu_style] 自动复核：%s' % result)
        try:
            self._task = asyncio.create_task(self._loop())
        except Exception as exc:
            logger.warning("[dafeiyu_style] 后台任务启动失败：%s" % str(exc)[:120])

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.store.close()

    # ---------- 学习 ----------

    async def _ask(self, system_prompt: str, user_prompt: str, stage: str) -> str:
        provider_id = str(
            self.cfg.get("coarse_provider_id" if stage == "coarse" else "refine_provider_id")
            or self.cfg.get("refine_provider_id") or self.cfg.get("coarse_provider_id") or ""
        ).strip()
        if provider_id not in ("deepseek/deepseek-flash", zhipu.PROVIDER):
            raise RuntimeError("风格学习模型不在已核验名单中")
        if not provider_id:
            raise RuntimeError("未配置 %s provider" % stage)

        async def _call():
            if provider_id == zhipu.PROVIDER:
                return await zhipu.ask(self.context,system_prompt,user_prompt,
                    self.cfg.get('max_tokens',1200),self.cfg.get('llm_timeout_s',90),
                    lambda response:record_zhipu_budget(response,self._budget_origin))
            return await llm_ask(
                self.context, provider_id, system_prompt, user_prompt,
                timeout=float(self.cfg.get("llm_timeout_s", 90.0)),
                max_tokens=int(self.cfg.get("max_tokens", 1200)),
                on_response=lambda response: record_budget(response, self._budget_origin),
            )

        if guarded_model_call is None or scope is None:
            raise ModelBudgetBlocked("预算运行时不可用，暂停学习")
        return await guarded_model_call(_call)

    async def _learn_group(self, group_id: str) -> dict:
        async with self._learning_lock:
            started=time.monotonic()
            total={'rules':0,'jargon':0,'few_shots':0}
            batches=0;result={}
            for _ in range(int(self.cfg.get('max_batches_per_run',10))):
                if time.monotonic()-started >= float(self.cfg.get('run_seconds',180)):
                    result={'error':'time_limit'};break
                result=await self._learn_batch(group_id)
                for key,value in (result.get('added') or {}).items():total[key]+=value
                if not result.get('advanced'):break
                batches+=1
                await asyncio.sleep(.1)
            else:result={'error':'batch_limit'}
            summary={**result,'advanced':batches>0,'batches':batches,'added':total}
            logger.info('[dafeiyu_style] 分批学习结束：batches=%s reason=%s added=%s' % (batches,result.get('error') or 'ok',total))
            return summary

    async def _learn_batch(self, group_id: str) -> dict:
        self._reload_cfg()
        if not self.cfg.get("enabled") or not self._group_allowed(group_id):
            return {"advanced": False, "error": "disabled_or_scope"}
        day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        quota_key = 'batch_count:' + group_id + ':' + day
        used = int(self.store.get_meta(quota_key, '1' if self.store.get_meta('learn_day:' + group_id)==day else '0'))
        if used >= int(self.cfg.get('daily_batch_cap',10)):
            return {"advanced": False, "error": "daily_batch_cap"}
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo') as f:
                available = next(int(line.split()[1]) for line in f if line.startswith('MemAvailable:'))
            if available < 600 * 1024:
                return {"advanced": False, "error": "low_memory"}
        cursor = int(self.store.get_meta("cursor:" + group_id, "0"))
        rows, _ = await asyncio.to_thread(_learning.read_events, self.memory_db, cursor,
            [group_id], int(self.cfg["min_msg_len"]), int(self.cfg["max_msg_len"]),
            int(self.cfg["max_batch"]))
        if len(rows) < int(self.cfg['min_messages']):
            return {"advanced": False, "error": "too_few", "added": {}}
        origins = {str(r["session_id"]) for r in rows}
        if len(origins) != 1 or scope is None:
            return {"advanced": False, "error": "missing_or_ambiguous_origin"}
        origin = next(iter(origins))
        if ':GroupMessage:' not in origin or origin != group_id:
            return {"advanced": False, "error": "invalid_origin"}
        # Reserve this day's attempt before calls; do not retry on exhausted budget.
        if len(rows) >= int(self.cfg['min_messages']):
            self.store.set_meta("learn_day:" + group_id, day)
            self.store.set_meta(quota_key,used+1)
        with scope(origin):
            self._budget_origin = origin
            return await self._learn_group_impl(group_id)

    async def _learn_group_impl(self, group_id: str) -> dict:
        cursor = int(self.store.get_meta("cursor:%s" % group_id, "0") or 0)
        try:
            result = await _learning.process_group(
                db_path=self.memory_db, group_id=group_id, since_id=cursor,
                cfg=self.cfg, ask=self._ask, store=self.store,
            )
        except ModelBudgetBlocked:
            logger.info("[dafeiyu_style] 预算已用完，本轮学习跳过：%s" % group_id)
            return {"advanced": False, "error": "budget"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[dafeiyu_style] 群 %s 学习失败：%s" % (group_id, str(exc)[:150]))
            return {"advanced": False, "error": "exception"}

        if result.get("advanced"):
            self.store.set_meta("cursor:%s" % group_id, result["max_id"])
        added = result.get("added") or {}
        if any(added.values()):
            logger.info("[dafeiyu_style] 群 %s 新增 规则%s 黑话%s 示例%s" % (
                group_id, added.get("rules", 0), added.get("jargon", 0),
                added.get("few_shots", 0)))
        return result

    async def _run_all(self):
        groups = [str(g) for g in (self.cfg.get("groups") or [])]
        if not groups:
            groups = await asyncio.to_thread(_learning.read_groups, self.memory_db)
        for group_id in groups:
            if not self._group_allowed(group_id):
                continue
            await self._learn_group(group_id)

    async def _loop(self):
        while True:
            try:
                self._reload_cfg()
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                today = now.strftime("%Y%m%d")
                request_id = self.cfg.get('catchup_request_id','')
                if self.cfg.get('enabled') and request_id and self.store.get_meta('catchup_last_id') != request_id:
                    self.store.set_meta('catchup_last_id',request_id)
                    await self._run_all()
                if (self.cfg.get("enabled", False)
                        and int(now.hour) == int(self.cfg.get("hour", 3))
                        and self.store.get_meta("last_run_day") != today):
                    self.store.set_meta("last_run_day", today)
                    await self._run_all()
                    last_cleanup = float(self.store.get_meta("last_cleanup_ts", "0") or 0)
                    if time.time() - last_cleanup > 30 * 86400:
                        removed = self.store.cleanup_rejected(30)
                        self.store.set_meta("last_cleanup_ts", str(int(time.time())))
                        if removed:
                            logger.info("[dafeiyu_style] 清理已驳回条目 %s 条" % removed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[dafeiyu_style] 后台循环异常：%s" % str(exc)[:150])
            await asyncio.sleep(TICK_SECONDS)

    # ---------- 注入 ----------

    @filter.on_llm_request(priority=97)
    async def inject_style(self, event: AstrMessageEvent, req):
        try:
            self._reload_cfg()
            if not self.cfg.get("enabled", False) or not self.cfg.get("inject_enabled", False):
                return
            group_id = self._event_group(event)
            if not group_id or not self._group_allowed(group_id):
                return
            rules = self.store.approved("style_rules", group_id, int(self.cfg.get("inject_rules", 5)))
            jargon = self.store.approved("jargon", group_id, int(self.cfg.get("inject_jargon", 5)))
            shots = self.store.approved("few_shots", group_id, int(self.cfg.get("inject_shots", 3)))
            if not (rules or jargon or shots):
                return
            block = await asyncio.wait_for(
                asyncio.to_thread(_inject.build_block, rules, jargon, shots,
                                  int(self.cfg.get("inject_max_chars", 600))),
                timeout=float(self.cfg.get("hook_timeout_s", 1.2)),
            )
            if not block:
                return
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
        except Exception as exc:
            logger.debug("[dafeiyu_style] 注入跳过：%s" % str(exc)[:120])

    # ---------- 命令 ----------

    def _denied(self, event: AstrMessageEvent) -> bool:
        return bool(self.cfg.get("admin_only", True)) and not event.is_admin()

    def _fmt_row(self, row) -> str:
        if "rule" in row.keys():
            text = row["rule"]
        elif "term" in row.keys():
            text = "%s = %s" % (row["term"], row["meaning"])
        else:
            text = "%s → %s" % (row["user_text"], row["bot_reply"])
        evidence = str(row["evidence"] or "") if "evidence" in row.keys() else ""
        return "#%s %s%s" % (row["id"], text[:60], ("｜依据：" + evidence[:40]) if evidence else "")

    @filter.command("风格")
    async def style_cmd(self, event: AstrMessageEvent, arg: GreedyStr):
        """/风格 [状态|列表|黑话|示例|通过|拒绝|导出|跑一次]"""
        event.stop_event()
        if self._denied(event):
            return
        self._reload_cfg()
        group_id = self._event_group(event)
        if not group_id:
            yield event.plain_result('请在群内使用风格命令；无法确认来源时不会学习。')
            return
        tokens = (arg or "").strip().split()
        head = tokens[0] if tokens else "状态"

        if head in ("状态", ""):
            counts = self.store.counts(group_id)
            cursor = self.store.get_meta("cursor:%s" % group_id, "0") if group_id else "-"
            providers = "粗筛=%s｜提炼=%s" % (
                self.cfg.get("coarse_provider_id") or "未配置",
                self.cfg.get("refine_provider_id") or "未配置")
            yield event.plain_result(
                "风格学习｜启用=%s 注入=%s\n待审：规则%s 黑话%s 示例%s\n游标=%s 上次运行=%s\n%s\n数据源=%s" % (
                    "开" if self.cfg.get("enabled") else "关",
                    "开" if self.cfg.get("inject_enabled") else "关",
                    counts["style_rules"], counts["jargon"], counts["few_shots"],
                    cursor, self.store.get_meta("last_run_day", "从未"), providers,
                    "在线" if os.path.exists(self.memory_db) else "缺失"))
            return

        if head == "列表":
            rows = self.store.pending("style_rules", group_id, 10)
            yield event.plain_result("待审规则：\n" + "\n".join(self._fmt_row(r) for r in rows)
                                     if rows else "暂无待审规则。")
            return

        if head in ("黑话", "示例"):
            table = "jargon" if head == "黑话" else "few_shots"
            if len(tokens) >= 3 and tokens[1] in ("通过", "拒绝"):
                try:
                    row_id = int(tokens[2])
                except ValueError:
                    yield event.plain_result("编号必须是数字。")
                    return
                action = "approve" if tokens[1] == "通过" else "reject"
                ok = self.store.review(table, row_id, action, str(event.get_sender_id() or ""))
                yield event.plain_result("已%s #%s" % (tokens[1], row_id)
                                         if ok else "没有这条待审记录（或已处理过）。")
                return
            rows = self.store.pending(table, group_id, 10)
            title = "待审黑话" if head == "黑话" else "待审示例"
            yield event.plain_result(title + "：\n" + "\n".join(self._fmt_row(r) for r in rows)
                                     if rows else "暂无待审%s。" % head)
            return

        if head in ("通过", "拒绝"):
            if len(tokens) < 2:
                yield event.plain_result("用法：/风格 通过 <编号>；黑话/示例用 /风格 黑话 通过 <编号>")
                return
            table = "style_rules"
            try:
                row_id = int(tokens[1])
            except ValueError:
                yield event.plain_result("编号必须是数字。")
                return
            action = "approve" if head == "通过" else "reject"
            actor = str(event.get_sender_id() or "")
            ok = self.store.review(table, row_id, action, actor)
            yield event.plain_result("已%s #%s" % ("通过" if action == "approve" else "驳回", row_id)
                                     if ok else "没有这条待审记录（或已处理过）。")
            return

        if head == "导出":
            data = {
                "rules": [dict(r) for r in self.store.approved("style_rules", group_id, 50)],
                "jargon": [dict(r) for r in self.store.approved("jargon", group_id, 50)],
                "few_shots": [dict(r) for r in self.store.approved("few_shots", group_id, 20)],
            }
            for item in data.values():
                for row in item:
                    row.pop("evidence", None)
            yield event.plain_result(json.dumps(data, ensure_ascii=False, indent=2)[:1800])
            return

        if head == "跑一次":
            if not group_id:
                yield event.plain_result("请在群里运行。")
                return
            result = await self._learn_group(group_id)
            added = result.get("added") or {}
            yield event.plain_result("本轮结果：批次=%s 状态=%s 规则+%s 黑话+%s 示例+%s" % (
                result.get('batches',0),result.get("error") or "ok", added.get("rules", 0),
                added.get("jargon", 0), added.get("few_shots", 0)))
            return

        yield event.plain_result("用法：/风格 [状态|列表|黑话|示例|通过|拒绝|导出|跑一次]")

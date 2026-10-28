"""大肥鱼长期记忆（A 方案）· 插件入口

要解决的问题：
  之前"记忆"全靠 livingmemory 的向量召回 + 定期摘要，效果是「记得结论，忘了经过」——
  "我叫什么名字"能答上，但"我上周说过想去漫展"就没了。而对话库本身只保留
  每会话最近 1000 条、且只删已总结的部分，老聊天会被滑出去。

这一层的定位（跟 livingmemory 明确分工，互不干扰）：
  · livingmemory  = 语义记忆：把聊天**压缩成事实**，负责"她记住了什么"
  · 本插件        = 情景记忆：把聊天**原样留档**，负责"当时到底说了什么"
  · 数据完全独立：自己的 memory.db；对 livingmemory 的 conversations.db 只读

四层：
  L0 events    原始聊天流水，永久、可全文检索（0 token）
  L1 people    人物主档：谁、多少条、什么时候来过、一句话画像
  L1 facts     人物事实时间线：喜好/关系/经历，只追加，冲突标记失效不删除
  L3 digests   日纪要（压缩层，可选；L0 才是主料）

成本：
  · 归档 = 0 token（纯 sqlite）
  · 检索 = 0 token（纯 FTS）
  · 只有"提炼人物事实/画像"要调模型，走 MiMo flash，一天一次、只跑增量

命令：
  /记忆 状态     看归档了多少、认识多少人
  /记忆 同步     立刻同步一次（默认后台每 10 分钟自动同步）
  /记忆 回填     从现有聊天重建人物计数（幂等，安全）
  /记忆 找 <人>  看档案
"""
import asyncio
import os
import traceback

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .archiver import sync
from .extract import extract_for_person
from .recall import recall_chat, recall_person, fmt_person
from .store import Store

PLUGIN_NAME = "dafeiyu_daily_memory"
SYNC_INTERVAL = 600  # 后台同步间隔（秒）；纯 sqlite 读写，不产生 token


@register("astrbot_plugin_dafeiyu_daily_memory", "TOKI", "v0.1.0",
          "大肥鱼情景记忆：原样归档群聊 + 人物档案时间线，可全文检索（与 livingmemory 独立）")
class DailyMemoryPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = str(StarTools.get_data_dir(PLUGIN_NAME))
        # 插件数据目录是 <astrbot_data>/plugin_data/<name>，上两级就是 astrbot_data
        self.astrbot_data = os.path.dirname(os.path.dirname(self.data_dir))
        self.store = Store(os.path.join(self.data_dir, "memory.db"))
        self._task = None
        self._sync_lock = asyncio.Lock()

    async def initialize(self):
        # 老库升级/首次安装：people 表可能是空的（有 events 但没建过人物），
        # 先补齐一次，免得 /记忆 找 和 list_people 一开始全是空的
        try:
            if self.store.count_events() > 0 and \
                    self.store.db.execute("SELECT COUNT(*) c FROM people").fetchone()["c"] == 0:
                await asyncio.to_thread(self._rebuild_people)
        except Exception as e:
            logger.warning("[daily_memory] 初始化重建人物失败：%s" % str(e)[:150])
        if self.config.get("auto_sync", True):
            self._ensure_loop()

    # ---------- 后台同步 ----------

    def _ensure_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        await asyncio.sleep(20)  # 让 AstrBot 自己先启动完
        while True:
            try:
                await self._do_sync()
                await self._maybe_daily_extract()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("[daily_memory] 后台同步失败：%s" % traceback.format_exc()[:400])
            await asyncio.sleep(int(self.config.get("sync_interval", SYNC_INTERVAL)))

    async def _maybe_daily_extract(self):
        """每天过了 extract_hour 就跑一次增量提炼。靠 meta 里的日期做幂等。"""
        if not self.config.get("extract_enabled", False):
            return
        import time as _t
        now = _t.time()
        st = _t.gmtime(now + 8 * 3600)
        today = _t.strftime("%Y-%m-%d", st)
        if st.tm_hour < int(self.config.get("extract_hour", 5)):
            return
        if self.store.get_meta("last_extract_day") == today:
            return
        self.store.set_meta("last_extract_day", today)
        pid = str(self.config.get("extract_provider_id", "xiaomi/mimo-v2.6-flash"))
        people = self.store.db.execute(
            "SELECT * FROM people WHERE msg_count>=3 ORDER BY msg_count DESC"
        ).fetchall()
        done, facts = 0, 0
        for p in people:
            n, _, err = await extract_for_person(self.context, self.store, p, pid)
            if not err:
                done += 1
                facts += n
            await asyncio.sleep(1.5)  # 别把旁路模型打满
        if done:
            logger.info("[daily_memory] 每日提炼：%d 人，新增 %d 条事实" % (done, facts))

    async def _do_sync(self, full=False):
        async with self._sync_lock:
            added, scanned, err = await asyncio.to_thread(
                sync, self.store, self.astrbot_data, full
            )
            # 有新人/新消息就顺手重算人物计数：纯 SQL，几十毫秒，
            # 但能保证 list_people / recall_person 随时可用（不用等手工回填）
            if added and not err:
                try:
                    await asyncio.to_thread(self._rebuild_people)
                except Exception as e:
                    logger.warning("[daily_memory] 重建人物失败：%s" % str(e)[:150])
        if err:
            logger.warning("[daily_memory] %s" % err)
        elif added:
            logger.info("[daily_memory] 归档 +%d 条（扫描 %d）" % (added, scanned))
        return added, scanned, err

    def _rebuild_people(self):
        """线程内重建（用自己的连接）。"""
        c = self.store.clone()
        try:
            return c.rebuild_people_from_events()
        finally:
            c.close()

    # ---------- 命令（0 token：返回纯文本，不进模型） ----------

    @filter.command("记忆")
    async def mem_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """记忆：/记忆 状态 | /记忆 同步 | /记忆 回填 | /记忆 找 <人>"""
        event.stop_event()
        arg = (arg or "").strip()
        if arg in ("同步", "sync"):
            added, scanned, err = await self._do_sync()
            if err:
                yield event.plain_result("同步失败：%s" % err)
            else:
                yield event.plain_result(
                    "同步完成：新增 %d 条（扫了 %d 条）。库里共 %d 条。"
                    % (added, scanned, self.store.count_events())
                )
            return
        if arg in ("回填", "rebuild"):
            n = await asyncio.to_thread(self._rebuild_people)
            yield event.plain_result("人物计数已重建，共 %d 人。" % n)
            return
        if arg.startswith("找"):
            who = arg[1:].strip()
            profile, recent = recall_person(self.store, who)
            if not profile:
                yield event.plain_result("没找到「%s」。" % who)
                return
            text = profile
            if recent:
                text += "\n\n最近说过：\n" + recent
            yield event.plain_result(text[:1800])
            return
        if arg.startswith("提炼"):
            who = arg[2:].strip()
            pid = str(self.config.get("extract_provider_id", "xiaomi/mimo-v2.6-flash"))
            if who in ("全部", "all", ""):
                yield event.plain_result("开始批量提炼全部成员，可能要几分钟…")
                ok, fail, total_facts = 0, 0, 0
                for p in self.store.db.execute(
                    "SELECT * FROM people ORDER BY msg_count DESC"
                ).fetchall():
                    if (p["msg_count"] or 0) < 3:
                        continue
                    n, _, err = await extract_for_person(self.context, self.store, p, pid)
                    if err:
                        fail += 1
                    else:
                        ok += 1
                        total_facts += n
                yield event.plain_result(
                    "批量提炼完成：%d 人成功（新增 %d 条事实），%d 人失败。"
                    % (ok, total_facts, fail))
                return
            p = self.store.get_person(who)
            if p is None:
                yield event.plain_result("没找到「%s」。" % who)
                return
            yield event.plain_result("正在提炼「%s」的档案…" % p["name"])
            n, changed, err = await extract_for_person(self.context, self.store, p, pid)
            if err:
                yield event.plain_result("提炼失败：%s" % err)
                return
            p2 = self.store.get_person(p["sender_id"])
            yield event.plain_result(
                "提炼完成：新增 %d 条事实%s\n\n%s"
                % (n, "，画像已更新" if changed else "",
                   fmt_person(self.store, p2))
            )
            return
        # 默认：状态
        total = self.store.count_events()
        ppl = self.store.db.execute("SELECT COUNT(*) c FROM people").fetchone()["c"]
        last = self.store.get_meta("sync_ts", 0)
        import time as _t
        when = _t.strftime("%m-%d %H:%M", _t.gmtime(float(last) + 8 * 3600)) if last else "从未"
        yield event.plain_result(
            "情景记忆状态\n"
            "· 归档聊天：%d 条\n"
            "· 认识的人：%d 位\n"
            "· 上次同步：%s（后台每 %d 分钟自动同步）\n"
            "用法：/记忆 找 <名字>"
            % (total, ppl, when, int(self.config.get("sync_interval", SYNC_INTERVAL)) // 60)
        )

    # ---------- 自动注入（让"她记得这个人"不依赖模型主动调工具） ----------

    @filter.on_llm_request(priority=96)
    async def inject_speaker_profile(self, event: AstrMessageEvent, req):
        """把**当前说话人**的档案塞到用户消息末尾。

        为什么放末尾而不是 system：
          前缀缓存按"前面这段一字不差"命中，档案里带时间/计数，塞进 system
          会让每次请求的前缀都变，缓存直接失效。挂到用户消息末尾（后缀）
          就不参与前缀，缓存不受影响 —— 这也是 livingmemory 的做法。

        只注入当前说话人，不注入别人：既省 token，也避免把 A 的隐私说给 B。
        """
        try:
            if not self.config.get("inject_profile", True):
                return
            person = None
            sid = ""
            try:
                sid = str(event.get_sender_id() or "")
            except Exception:
                sid = ""
            if sid:
                person = self.store.db.execute(
                    "SELECT * FROM people WHERE sender_id=?", (sid,)
                ).fetchone()
            if person is None:
                # 平台给的 ID 与归档里的对不上时，退一步按昵称找
                try:
                    name = str(event.get_sender_name() or "")
                except Exception:
                    name = ""
                if name:
                    person = self.store.get_person(name)
            if person is None:
                return
            facts = self.store.get_facts(person["sender_id"], active_only=True, limit=8)
            if not person["summary"] and not facts:
                return

            lines = ["[你记得这个人（来自你们以前的聊天，不是本次对话）]"]
            lines.append("%s：%s" % (person["name"], person["summary"] or ""))
            if facts:
                by_cat = {}
                for f in facts:
                    by_cat.setdefault(f["category"] or "其他", []).append(f["fact"])
                for cat, items in by_cat.items():
                    lines.append("· %s：%s" % (cat, "；".join(items[:4])))
            lines.append("（自然地用上这些，别复述成档案条；和本轮话题无关就别硬提。）")
            text = "\n".join(lines)
            if len(text) > 700:
                text = text[:700] + "…"

            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(text=text).mark_as_temp())
        except Exception as e:
            logger.debug("[daily_memory] 注入档案失败：%s" % str(e)[:150])

    # ---------- LLM 工具（模型自己决定何时查） ----------

    @filter.llm_tool(name="recall_chat")
    async def tool_recall_chat(self, event: AstrMessageEvent, query: str = "",
                               person: str = "", days: int = 0, limit: int = 10) -> str:
        """检索过去的群聊原始记录（不是摘要，是当时的原话）。
        当需要回忆"谁在什么时候说过什么"、某件事的经过、某个梗/玩笑的出处、
        或对方提到"之前说过/上次聊的"而你手上没有这段对话时调用。

        Args:
            query (string): 关键词，多个字会按片段匹配；留空则只按人和时间过滤
            person (string): 说话人的名字或昵称，留空表示不限
            days (number): 只查最近几天，0 表示不限
            limit (number): 最多返回几条，默认 10，最多 30
        """
        try:
            n = max(1, min(int(limit or 10), 30))
            return recall_chat(self.store, query=query or None,
                               person=person or None,
                               days=int(days) if days else None, limit=n)
        except Exception as e:
            logger.warning("[daily_memory] recall_chat 出错：%s" % e)
            return "检索出错：%s" % str(e)[:120]

    @filter.llm_tool(name="recall_person")
    async def tool_recall_person(self, event: AstrMessageEvent, name: str) -> str:
        """查某个人的档案：别名、聊过多少、什么时候来的、已知的喜好/关系/经历，
        以及最近几句发言。当话题涉及具体某个人、要判断"他是谁/他喜欢什么/
        我们熟不熟"时调用。

        Args:
            name (string): 对方的昵称或名字
        """
        try:
            profile, recent = recall_person(self.store, name)
            if not profile:
                return "档案里没有「%s」。可以换个写法（昵称/ID），或用 recall_chat 按关键词找。" % name
            return profile + (("\n最近发言：\n" + recent) if recent else "")
        except Exception as e:
            logger.warning("[daily_memory] recall_person 出错：%s" % e)
            return "查询出错：%s" % str(e)[:120]

    async def terminate(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self.store.close()

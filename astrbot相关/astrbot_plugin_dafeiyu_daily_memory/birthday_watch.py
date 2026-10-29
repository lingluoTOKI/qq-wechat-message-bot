"""生日播报：每天定点把当天过生日的人，按人物画像写成祝贺词发到群里。

为什么独立成模块：
  · 抽生日（birthday.py）是 0 token 的确定性规则；写祝贺词要调一次模型，两者节奏不同；
  · 需要「每天一次 + 幂等 + 失败重试」，不能塞进 10 分钟一次的同步循环；
  · 模型不可用时必须有模板兜底，当天不能空过。

幂等：birthday_greetings(sender_id, year) 唯一；只有真正送达才落库，
      发送失败会在下一轮（5 分钟后）重试，最多 3 次。
"""

import asyncio
import os
import re
import sqlite3
import time

from astrbot.api import logger

from . import birthday, llm_client

TZ = 8 * 3600  # 容器是 UTC，展示/判定统一按北京时间
CHECK_SECONDS = 300
MAX_ATTEMPTS = 3

# 没有人格可读时的兜底口吻；正常情况下会用 AstrBot 里 persona 12（小洛）的完整人设
STYLE_FALLBACK = "你是小洛，蓝发小鲸鱼娘，QQ群里的普通群友，傲娇嘴硬心软，说话短、像随手打字。"

# 技术/调试类事实不进祝贺词：用户要的是生活化的碎碎念，不是复述插件改动
_TECH = re.compile(
    r"代码|脚本|配置|插件|缓存|token|调试|数据库|接口|部署|日志|函数|变量|"
    r"提示词|模型|服务器|机器人|bug|api|json|生日记录|命令|/说",
    re.I,
)

TASK = (
    "【本次任务】群里有成员今天过生日，你现在要直接在群里发一句话祝贺，不是在扮演旁白：\n"
    "· 以「小洛」开头，用第一人称说话（「哼，小洛可没忘」「小洛记着呢」），不要「小洛祝你」那种旁观腔；\n"
    "· 叫出对方名字或平时怎么叫的，话里要有「生日快乐」四个字；\n"
    "· 傲娇一点：嘴上嫌弃、心里在意，可以带「哼」「才不是特意记的」「就这一次」这种小别扭；\n"
    "· 生活化一点：聊吃饭、熬夜、追番、打牌、天气、群里的碎碎念这种日常，像身边熟人顺口唠叨；\n"
    "· 要想他真的在意的小事：他总熬夜、爱喝什么、说话习惯、常挂在嘴边的那句，捡一件轻轻提一下；\n"
    "· 别反复用同一个梗（「红温」「破防」这类），换成自己平时的说法，一件事只说一次；\n"
    "· 不要写技术内容：不出现代码、配置、插件、缓存、token、调试、数据库、机器人功能这些词，也别拿「记生日」这件事当梗；\n"
    "· 只写一句话，不超过 45 字，像微信里随手敲出来的一行，不要分行，别断在半句；\n"
    "· 不罗列祝福，不写「天天开心/万事如意/新的一岁」；不要表情符号、颜文字、{{...}} 占位符；\n"
    "· 直接输出这一行字，不要引号、不要解释。"
)


def _life_facts(facts):
    """过滤掉技术/调试类事实，只留能当生活细节用的。"""
    return [fact.strip() for fact in facts or [] if fact and not _TECH.search(fact)]


def _bj_now():
    return time.gmtime(time.time() + TZ)


def _md(value):
    """'11月7日' → (11, 7)；解析失败返回 None。"""
    value = birthday.normalize(value)
    if not value:
        return None
    body = value.replace("月", ",").replace("日", "")
    month, day = body.split(",")
    return int(month), int(day)


def upcoming(rows, today=None):
    """按「距下次生日还有几天」排序，返回 [(days, 行)]。"""
    now = today or _bj_now()
    year, month, day = now.tm_year, now.tm_mon, now.tm_mday
    out = []
    for row in rows:
        parsed = _md(row["birthday"])
        if not parsed:
            continue
        m, d = parsed
        target = (m, d)
        left = (target[0] - month) * 31 + (target[1] - day)
        if left < 0:
            left += 365  # 今年的已过，算到明年
        out.append((left, row))
    out.sort(key=lambda item: item[0])
    return out


def format_list(store, hour=9):
    """给 /记忆 生日 用的纯文本列表。"""
    rows = store.birthdays()
    if not rows:
        return "还没有记录任何人的生日。等本人说过「我的生日是X月Y日」就会自动记住。"
    lines = ["已知生日（%d 人）：" % len(rows)]
    for days, row in upcoming(rows)[:40]:
        when = "今天" if days == 0 else ("还有 %d 天" % days)
        lines.append("· %s %s（%s）" % (row["birthday"], row["name"], when))
    lines.append("到当天 %02d:00 后会自动发祝贺。" % int(hour))
    return "\n".join(lines)


def _fallback(person, facts):
    """兜底也要短、要像人话：细节太长就不硬塞，宁可只说一句关心。"""
    name = person["name"]
    short = [f.strip("。 「」") for f in facts if f and len(f.strip("。 ")) <= 12]
    if short:
        return "哼，%s 生日快乐。小洛记着呢——%s，今天别又熬到半夜" % (name, short[0])
    return "哼，%s 生日快乐。小洛才不是特意记的，就是今天别又熬到半夜了" % name


def _clean(text):
    """去掉人格模板里自带的 {{...}} 表情占位、说话人前缀、包裹引号和多行。"""
    text = re.sub(r"\{\{.*?\}\}", "", text or "")
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.strip().strip("「」\"'“”『』 ")
    text = re.sub(r"^小洛\s*[:：]\s*", "", text)  # 模型偶尔会带「小洛：」前缀
    return re.sub(r"\s{2,}", " ", text).strip()


# 祝贺词里不该出现的技术腔（只用于成品检查，不是事实过滤）
_BAD_OUT = re.compile(
    r"机器人|插件|代码|脚本|配置|数据库|缓存|调试|token|接口|日志|部署|提示词",
    re.I,
)

_CALL_HINT = re.compile(r"老大|大佬|佬|哥|姐|同学|老板")


def _called(text, name):
    """有没有叫到对方（名字，或平时惯用的称呼）。"""
    return bool(name and name in text) or bool(_CALL_HINT.search(text))


class BirthdayWatch:
    def __init__(self, plugin):
        self.plugin = plugin
        self._task = None
        self._attempts = {}
        self._warned = False
        self._persona = None

    @property
    def config(self):
        return getattr(self.plugin, "config", {}) or {}

    def enabled(self):
        return bool(self.config.get("birthday_enable", True))

    def hour(self):
        try:
            return max(0, min(23, int(self.config.get("birthday_hour", 9))))
        except (TypeError, ValueError):
            return 9

    def targets(self):
        raw = str(self.config.get("birthday_targets", "") or "")
        return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]

    def due(self):
        now = _bj_now()
        md = "%d月%d日" % (now.tm_mon, now.tm_mday)
        return self.plugin.store.db.execute(
            "SELECT * FROM people WHERE birthday=?", (md,)
        ).fetchall()

    # ---------- 生命周期 ----------

    def start(self):
        if not self.enabled():
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("[daily_memory] 生日播报已启动：%02d:00 后检查 · 目标 %s"
                        % (self.hour(), "、".join(self.targets()) or "未配置"))

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        self._task = None

    async def _loop(self):
        await asyncio.sleep(20)  # 让 AstrBot 自己先起完
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[daily_memory] 生日检查失败：%s" % str(e)[:200])
            await asyncio.sleep(CHECK_SECONDS)

    # ---------- 核心 ----------

    async def tick(self):
        if not self.enabled():
            return 0
        now = _bj_now()
        if now.tm_hour < self.hour():
            return 0
        people = self.due()
        if not people:
            return 0
        targets = self.targets()
        if not targets:
            if not self._warned:
                logger.warning("[daily_memory] 今天有 %d 人生日，但没配置 birthday_targets，先只记录不发送"
                               % len(people))
                self._warned = True
            return 0
        sent = 0
        for person in people:
            sid, year = person["sender_id"], now.tm_year
            if self.plugin.store.already_greeted(sid, year):
                continue
            key = (sid, year)
            if self._attempts.get(key, 0) >= MAX_ATTEMPTS:
                continue
            text = await self.compose(person)
            delivered = await self._send(text, targets)
            self._attempts[key] = self._attempts.get(key, 0) + 1
            if delivered:
                self.plugin.store.mark_greeted(sid, year)
                self._attempts.pop(key, None)
                sent += 1
                logger.info("[daily_memory] 生日祝贺已发：%s（%s）→ %s"
                            % (person["name"], person["birthday"], "、".join(delivered)))
            else:
                logger.warning("[daily_memory] 生日祝贺未送达：%s，稍后重试" % person["name"])
            await asyncio.sleep(1.5)
        return sent

    async def compose(self, person):
        """按画像生成一条祝贺；用 persona 12 的人设口吻，失败用模板兜底。"""
        store = self.plugin.store
        name = person["name"]
        facts = [row["fact"] for row in store.get_facts(person["sender_id"],
                                                       active_only=True, limit=12)]
        facts = _life_facts(facts)[:6]
        profile = (person["summary"] or "").strip()
        prompt = (
            "今天过生日：%s（%s）\n"
            "你对他的印象：%s\n"
            "你记得的细节：%s\n"
            "发一条群里的生日祝贺。"
            % (person["name"], person["birthday"], profile or "（没什么印象，是个熟面孔）",
               "；".join(facts) or "（想不起来什么特别的）")
        )
        provider = str(self.config.get("birthday_provider_id", "xiaomi/mimo-v2.6-flash"))
        body = ""
        for _ in range(3):
            try:
                raw = await llm_client.ask(self.plugin.context, provider,
                                           self.system_prompt(), prompt,
                                           timeout=45, max_tokens=700)
            except Exception as e:
                logger.warning("[daily_memory] 生成祝贺词失败，用模板兜底：%s" % str(e)[:120])
                break
            candidate = _clean(raw)
            if candidate:
                body = candidate
            # 模型偶尔只吐推理、正文为空；或忘了自称 / 没叫对方 / 又扯回技术话；重试后仍不行就用模板
            if body and "小洛" in body and not _BAD_OUT.search(body) and _called(body, name):
                break
        if not body or _BAD_OUT.search(body):
            body = _clean(_fallback(person, facts))
        elif "小洛" not in body:
            body = "小洛可记着呢，" + body
        if len(body) > 120:
            body = body[:120] + "…"
        return body

    def system_prompt(self):
        """人设 + 本次任务。人设从 AstrBot 的 personas 表只读；读不到就用兜底口吻。"""
        if self._persona is not None:
            return (self._persona + "\n\n" + TASK) if self._persona else (STYLE_FALLBACK + "\n\n" + TASK)
        self._persona = ""
        persona_id = str(self.config.get("birthday_persona_id", "12") or "12")
        path = os.path.join(getattr(self.plugin, "astrbot_data", "") or "", "data_v4.db")
        try:
            db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            row = db.execute("SELECT system_prompt FROM personas WHERE persona_id=?",
                             (persona_id,)).fetchone()
            db.close()
            if row and row[0]:
                self._persona = str(row[0]).strip()
        except Exception as e:
            logger.warning("[daily_memory] 读取人格 %s 失败：%s" % (persona_id, str(e)[:120]))
        return (self._persona + "\n\n" + TASK) if self._persona else (STYLE_FALLBACK + "\n\n" + TASK)

    async def _send(self, text, targets):
        delivered, failed = [], []
        for umo in targets:
            try:
                from astrbot.api.event import MessageChain
                from astrbot.api.message_components import Plain
                ok = await self.plugin.context.send_message(
                    umo, MessageChain(chain=[Plain(text)]))
                if ok is False:
                    raise RuntimeError("未找到匹配的平台")
                delivered.append(umo)
            except Exception:
                try:
                    from astrbot.api.event import MessageChain as MC
                    ok = await self.plugin.context.send_message(umo, MC().message(text))
                    if ok is False:
                        raise RuntimeError("未找到匹配的平台")
                    delivered.append(umo)
                except Exception as e:
                    failed.append("%s（%s）" % (umo, str(e)[:60]))
        if failed:
            logger.warning("[daily_memory] 生日祝贺送达失败：%s" % "；".join(failed))
        return delivered

    async def preview(self, name):
        """命令里预览某人的祝贺词（不发送）。"""
        person = self.plugin.store.get_person(name)
        if person is None:
            return "没找到「%s」。" % name
        value = (person["birthday"] or "").strip() or birthday.refresh(
            self.plugin.store, person["sender_id"])
        if not value:
            return "「%s」还没有生日记录。" % person["name"]
        return await self.compose(person)

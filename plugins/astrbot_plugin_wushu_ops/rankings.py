"""Read-only cumulative leaderboard queries; never infer player identities."""
import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .wushu_net import NetError


async def ranking_text(api, division="all", player="", limit=10):
    aliases = {"单人": "solo", "多人": "coop", "全部": "all"}
    division = aliases.get(division, division)
    if division not in {"solo", "coop", "all"}:
        return "分榜请选择：单人、多人或全部。"
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        return "显示条数须为1—20。"
    player = str(player or "").strip()
    if len(player) > 64:
        return "昵称请控制在64字以内。"
    divisions = ["solo", "coop"] if division == "all" else [division]

    async def board(d):
        label = "单人" if d == "solo" else "多人"
        try:
            data = await api.daily_challenges(d, ranking=True, timeout=8)
        except NetError as exc:
            return f"{label}榜查询失败：{exc.user_message()}"
        rows = data.get("rows")
        if data.get("rankingScope") != "cumulative" or not isinstance(rows, list):
            return f"{label}榜数据格式异常，暂不展示。"
        if any(not isinstance(r, dict) or type(r.get("rank")) is not int
               or r["rank"] < 1 or type(r.get("points")) is not int
               or r["points"] < 0 or not isinstance(r.get("name"), str) for r in rows):
            return f"{label}榜数据格式异常，暂不展示。"
        matches = [r for r in rows if player.casefold() in r["name"].casefold()] if player else rows
        title = f"{label}累计积分榜 · 数据日期 {data.get('day', '未知')}"
        if not rows:
            return title + "\n暂无上榜成绩。"
        if not matches:
            return title + "\n当前接口返回的榜单中未找到该昵称；不代表没有成绩。"
        lines = [title]
        for row in matches[:limit]:
            name = re.sub(r"[\x00-\x1f\x7f]", " ", row["name"])[:40]
            lines.append(f"第{row['rank']}名 · {name} · {row['points']}分")
        lines.append(f"展示{min(limit, len(matches))}/{len(matches)}条" + ("匹配记录（昵称包含查询；同名不合并）" if player else "返回记录"))
        return "\n".join(lines)

    boards = await asyncio.gather(*(board(d) for d in divisions))
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M:%S")
    return "\n\n".join(boards) + f"\n查询时间：{stamp}（北京时间）；累计积分，并列沿用官方名次。"

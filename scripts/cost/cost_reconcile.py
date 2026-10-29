"""成本对账：官方用量账单 vs 本地面板记录。

旧框架覆盖率诊断工具：不作为实时账单或预算来源。
新版逐请求账本使用 scripts/cost/import_official_usage.py 导入官方导出，
使用 scripts/cost/reconcile_request_ledger.py 按小时比较；不要再固定乘覆盖率系数。

用法：
    python scripts/cost/cost_reconcile.py <官方zip或目录> [日期]

官方 CSV 由 DeepSeek 平台「用量明细」导出，包含 amount-*.csv（token 与单价）
和 cost-*.csv（时段成本）。本脚本：
1. 按北京时间汇总官方 请求数 / 缓存命中 / 未命中 / 输出 / 成本；
2. 按同一口径汇总 supplement.db 中的记录并按官方单价重算；
3. 输出覆盖率（用于把"面板数字"折算成"真实账单"）。
"""
import datetime
import io
import os
import sqlite3
import sys
import zipfile

# 官方单价（人民币 / 百万 token）
PRICE_CACHED = 0.02
PRICE_MISS = 1.0
PRICE_OUTPUT = 4.0
PEAK_MULT = 2.0
PEAK_WINDOWS = ((9, 12), (14, 18))  # 工作日北京时间

DB = os.environ.get(
    "COST_DB", "/opt/bot-stack/astrbot/data/plugin_data/astrbot_plugin_cost_control/supplement.db")


def read_official(path):
    """读取官方导出（zip 或目录），返回 {日期: {req, cached, miss, out, cost}}。

    官方 CSV 结构：
    - amount-*.csv: user_id,start,end,model,name,api_key_name,api_key,type,price,amount
      其中 type ∈ REQUEST / INPUT_CACHE_HIT / INPUT_CACHE_MISS / OUTPUT，amount 为数量；
    - cost-*.csv: user_id,start,end,model,name,wallet_type,cost,currency
    """
    entries = []
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.endswith(".csv"):
                with io.open(os.path.join(path, name), encoding="utf-8-sig") as f:
                    entries.append((name, f.read()))
    else:
        zf = zipfile.ZipFile(path)
        for name in zf.namelist():
            if name.endswith(".csv"):
                entries.append((name, zf.read(name).decode("utf-8-sig")))

    agg = {}
    for name, text in entries:
        lines = text.strip().splitlines()
        if len(lines) < 2:
            continue
        head = [h.strip() for h in lines[0].split(",")]
        kind = "cost" if "cost" in name else "amount"
        idx = {h: i for i, h in enumerate(head)}
        for line in lines[1:]:
            cols = [x.strip() for x in line.split(",")]
            if len(cols) < len(head):
                continue
            stamp = cols[idx.get("start_time_iso", 0)]
            day = stamp[:10]
            bucket = agg.setdefault(day, {"req": 0, "cached": 0, "miss": 0, "out": 0, "cost": 0.0})
            if kind == "cost":
                bucket["cost"] += float(cols[idx["cost"]])
                continue
            typ = cols[idx.get("type", 7)].lower()
            n = int(cols[idx.get("amount", len(cols) - 1)])
            if typ == "request_count":
                bucket["req"] += n
            elif typ == "input_cache_hit_tokens":
                bucket["cached"] += n
            elif typ == "input_cache_miss_tokens":
                bucket["miss"] += n
            elif typ == "output_tokens":
                bucket["out"] += n
    return agg


def panel_cost(day):
    """按北京时间汇总 supplement.db，并按官方单价重算成本。"""
    c = sqlite3.connect(DB)
    rows = c.execute(
        "select created_at, token_input_cached, token_input_other, token_output "
        "from cost_supplements").fetchall()
    out = {"req": 0, "cached": 0, "miss": 0, "out": 0, "cost": 0.0}
    for ts, tc, tu, to in rows:
        utc = datetime.datetime.fromisoformat(str(ts))
        local = utc + datetime.timedelta(hours=8)
        if local.strftime("%Y-%m-%d") != day:
            continue
        mult = 1.0
        if local.weekday() < 5 and any(s <= local.hour < e for s, e in PEAK_WINDOWS):
            mult = PEAK_MULT
        cost = (int(tc or 0) * PRICE_CACHED + int(tu or 0) * PRICE_MISS
                + int(to or 0) * PRICE_OUTPUT) / 1e6 * mult
        out["req"] += 1
        out["cached"] += int(tc or 0)
        out["miss"] += int(tu or 0)
        out["out"] += int(to or 0)
        out["cost"] += cost
    c.close()
    return out


HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost_coverage.json")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    save = "--save" in sys.argv
    only_day = next((a for a in sys.argv[1:] if a[:2] == "20" and len(a) == 10), None)
    official = read_official(path)
    days = [only_day] if only_day else sorted(official)[-7:]
    print("%-12s %6s %6s %9s %9s %7s %7s" % ("日期", "官方req", "面板req", "覆盖率", "官方¥", "面板¥", "成本覆盖率"))
    history = {}
    if os.path.exists(HISTORY):
        import json
        with io.open(HISTORY, encoding="utf-8") as f:
            history = json.load(f)

    for day in days:
        o = official.get(day, {"req": 0, "cost": 0.0})
        p = panel_cost(day)
        req_rate = (p["req"] / o["req"] * 100) if o.get("req") else 0
        cost_rate = (p["cost"] / o["cost"] * 100) if o.get("cost") else 0
        print("%-12s %6d %6d %8.0f%% %9.4f %7.4f %10.0f%%" % (
            day, o.get("req", 0), p["req"], req_rate, o.get("cost", 0.0), p["cost"], cost_rate))
        history[day] = {
            "official_req": o.get("req", 0), "panel_req": p["req"],
            "official_cost": round(o.get("cost", 0.0), 6), "panel_cost": round(p["cost"], 6),
            "req_coverage": round(req_rate, 1), "cost_coverage": round(cost_rate, 1),
        }

    recent = [v["cost_coverage"] for k, v in sorted(history.items())[-7:] if v["cost_coverage"]]
    if recent:
        avg = sum(recent) / len(recent)
        print("\n近 %d 日平均成本覆盖率：%.1f%%" % (len(recent), avg))
        print("账单口径 ≈ 面板数字 ÷ %.3f（例：面板 ¥0.50 → 账单 ¥%.2f）" % (avg / 100, 0.50 / (avg / 100)))

    if save:
        import json
        with io.open(HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)
        print("已写入 %s" % HISTORY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

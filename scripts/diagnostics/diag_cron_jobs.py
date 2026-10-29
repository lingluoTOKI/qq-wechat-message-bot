#!/usr/bin/env python3
"""列出 AstrBot 内部 cron 任务，判断是否有高频任务在烧 token。"""
import json
import sqlite3

DB = "file:/AstrBot/data/data_v4.db?mode=ro"


def main():
    c = sqlite3.connect(DB, uri=True, timeout=5)
    cols = [r[1] for r in c.execute("pragma table_info(cron_jobs)")]
    print("列:", cols)
    for row in c.execute("select * from cron_jobs"):
        rec = dict(zip(cols, row))
        print("=" * 70)
        for k in ("name", "description", "job_type", "cron_expression", "enabled",
                  "last_run_at", "next_run_at", "run_count", "last_status",
                  "session_id", "created_at"):
            if k in rec:
                v = rec[k]
                if isinstance(v, str) and len(v) > 160:
                    v = v[:160] + "…"
                print("  %-16s = %s" % (k, v))
    c.close()


if __name__ == "__main__":
    main()

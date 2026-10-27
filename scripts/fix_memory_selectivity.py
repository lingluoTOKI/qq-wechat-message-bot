#!/usr/bin/env python3
"""让记忆召回真正"按相关性"选择：关掉那个不看相关性的时间槽。

原理（memory_engine_write_ops.py:178）：
    recent_count = min(max(0, recent_memory_count), k)
    selected = results[: max(0, k - recent_count)]   # k=3,recent=1 → 只取 2 条相关的
    for result in recent:                            # 再把"最新 1 条"硬塞进来
        if result.doc_id not in selected_ids: selected.append(result)

即：k=3 里有 1 格被"最近 72 小时内最新的一条"占着，**完全不看相关性**。
关掉它（1 -> 0）后，3 格全部由相关性决定；不相关就少注入甚至不注入。

保守起见**不动** min_similarity_for_retrieval(0.45)：
代码里相似度闸门带 `and signals` 条件（无向量分则整条跳过），
调高门槛的实际效果未经实测，贸然改可能把召回打死。
"""
import json
import shutil
from datetime import datetime

C = "/opt/bot-stack/astrbot/data/config/astrbot_plugin_livingmemory_config.json"


def main():
    raw = open(C, "rb").read()
    bom = raw[:3] == b"\xef\xbb\xbf"
    shutil.copy(C, "%s.bak-recent-%s" % (C, datetime.now().strftime("%Y%m%d-%H%M")))

    d = json.load(open(C, encoding="utf-8-sig"))
    re_ = d.setdefault("recall_engine", {})

    print("改前:")
    for k in ("top_k", "recent_memory_count", "min_similarity_for_retrieval",
              "min_importance_for_retrieval"):
        print("  %-32s = %s" % (k, re_.get(k)))

    old = re_.get("recent_memory_count")
    re_["recent_memory_count"] = 0

    with open(C, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print("\n改后: recent_memory_count = %s -> 0（不再硬塞不看相关性的最新一条）" % old)
    print("      top_k 保持 %s，全部按相关性决定" % re_.get("top_k"))


if __name__ == "__main__":
    main()

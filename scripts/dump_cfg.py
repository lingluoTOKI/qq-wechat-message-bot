import json
d = json.load(open("/opt/bot-stack/astrbot/data/cmd_config.json", encoding="utf-8"))
print("=== provider_sources ===")
for s in d.get("provider_sources", []):
    ks = {}
    for k, v in s.items():
        if isinstance(v, str) and len(v) > 16:
            ks[k] = v[:10] + "...(%d)" % len(v)
        else:
            ks[k] = v
    print(" ", ks)
print("=== provider ===")
for p in d.get("provider", []):
    print("  ", p.get("id"), "|", list(p.keys()))

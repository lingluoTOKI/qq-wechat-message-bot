import json, glob, os
for p in glob.glob('/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/*.json'):
    d = json.load(open(p, encoding='utf-8'))
    if isinstance(d, dict):
        keys = [k for k in d if 'rank' in k or 'attempt' in k]
        for k in keys:
            del d[k]
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f"{p}: cleared {keys}")

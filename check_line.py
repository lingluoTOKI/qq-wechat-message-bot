p = "/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/assistant_runtime.py"
lines = open(p, encoding="utf-8").readlines()
for i, line in enumerate(lines):
    if 'sections.append' in line and '🏆' in line:
        print(f"line {i+1}: {repr(line)}")

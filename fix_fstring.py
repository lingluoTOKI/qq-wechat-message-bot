p = "/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/assistant_runtime.py"
s = open(p, encoding="utf-8").read()

# 修复断行的 f-string
broken = '''                sections.append(f'🏆 {label}榜第一：{names}（{leaders[0]["points"]}分）
{phrase}')'''
fixed = """                sections.append(f'🏆 {label}榜第一：{names}（{leaders[0]["points"]}分）\\n{phrase}')"""

if broken in s:
    s = s.replace(broken, fixed)
    print("fixed broken line")
else:
    print("broken pattern not found, trying alternative...")
    # 看看实际内容
    lines = s.split('\n')
    for i in range(229, 235):
        print(f"{i+1}: {repr(lines[i])}")

open(p, "w", encoding="utf-8").write(s)

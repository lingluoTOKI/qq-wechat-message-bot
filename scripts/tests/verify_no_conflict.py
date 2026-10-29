import sys
sys.path.insert(0, "/AstrBot/data/plugins")
from astrbot.core.star.star_handler import star_handlers_registry

say = []
gsv = []
for h in star_handlers_registry:
    name = getattr(h, "handler_name", "") or ""
    mod = getattr(h, "handler_module_path", "") or ""
    if "say_cmd" in name:
        say.append((name, mod))
    if "gsv_tts" in name or "GPT_SoVITS" in mod or "GPT_SoVITS" in name:
        gsv.append((name, mod))

print("注册的 /说 命令 handler:")
for n, m in say:
    print("   %-18s <- %s" % (n, m))
print("  共 %d 个（应为 1）" % len(say))

print("\n官方 GPT_SoVITS 残留 handler:")
for n, m in gsv:
    print("   %-18s <- %s" % (n, m))
print("  共 %d 个（应为 0）" % len(gsv))

assert len(say) == 1, "命令重复！"
assert len(gsv) == 0, "官方插件仍有注册！"
print("\n无冲突: PASS")

import sys
sys.path.insert(0, "/AstrBot/data/plugins")

# 只导入 clonetts（模拟"官方插件已停用"后的情况），看命令注册结果
import astrbot_plugin_clonetts.main as m
from astrbot.core.star.star_handler import star_handlers_registry

say = [(getattr(h, "handler_name", ""), getattr(h, "handler_module_path", ""))
       for h in star_handlers_registry
       if "say_cmd" in (getattr(h, "handler_name", "") or "")]
print("clonetts 注册的 /说 命令:")
for n, p in say:
    print("   %-14s <- %s" % (n, p))
print("  共 %d 个" % len(say))
assert len(say) == 1, "应恰好 1 个"

# 官方插件未被导入 → 其说命令不会注册
print("\n官方插件已导入?", "astrbot_plugin_GPT_SoVITS.main" in sys.modules)
assert "astrbot_plugin_GPT_SoVITS.main" not in sys.modules
print("/说 命令唯一: PASS")

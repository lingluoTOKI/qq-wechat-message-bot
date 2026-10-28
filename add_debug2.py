p = "/opt/bot-stack/astrbot/data/plugins/astrbot_plugin_wushu_ops/assistant_runtime.py"
s = open(p, encoding="utf-8").read()

old = """    async def rankings(self, force=False):
        targets = [t for t in self.plugin._targets() if ':GroupMessage:' in t and t.startswith('大肥鱼-官方:')]"""

new = """    async def rankings(self, force=False):
        print(f"[rank-debug] rankings called, force={force}")
        targets = [t for t in self.plugin._targets() if ':GroupMessage:' in t and t.startswith('大肥鱼-官方:')]
        print(f"[rank-debug] targets={targets}")"""

s = s.replace(old, new)

# 也在 rank-attempt 那里加
old2 = """        if self.get('rank-attempt') == day:
            return"""
new2 = """        if self.get('rank-attempt') == day:
            print(f"[rank-debug] rank-attempt cache hit, skip")
            return"""
s = s.replace(old2, new2)

open(p, "w", encoding="utf-8").write(s)
print("done")

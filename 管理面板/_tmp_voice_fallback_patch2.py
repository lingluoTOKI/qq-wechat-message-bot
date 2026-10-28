import py_compile

p = "/AstrBot/astrbot/core/platform/sources/qqofficial/qqofficial_message_event.py"
s = open(p, encoding="utf-8").read()

old = '''                    payload["file_type"] = 4
                    result = await self.bot.api._http.request(route, json=payload)'''

new = '''                    payload["file_type"] = 4
                    # \u964d\u7ea7\u6210\u6587\u4ef6\u65f6\u628a .silk \u540d\u6539\u56de .mp3\uff08\u5185\u5bb9\u672c\u5c31\u662f mp3\uff09\uff0c
                    # \u5426\u5219\u7528\u6237\u4e0b\u5230\u4e00\u4e2a\u65e0\u6cd5\u76f4\u63a5\u64ad\u653e\u7684 .silk \u6587\u4ef6\u3002
                    _fn = payload.get("file_name")
                    if _fn and str(_fn).lower().endswith(".silk"):
                        payload["file_name"] = str(_fn)[:-5] + ".mp3"
                    result = await self.bot.api._http.request(route, json=payload)'''

n = s.count(old)
print("occurrences =", n)
if n != 1:
    raise SystemExit("pattern not unique, abort")
open(p, "w", encoding="utf-8").write(s.replace(old, new))
py_compile.compile(p, doraise=True)
print("FILENAME PATCH OK")

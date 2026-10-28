import py_compile

p = "/AstrBot/astrbot/core/platform/sources/qqofficial/qqofficial_message_event.py"
s = open(p, encoding="utf-8").read()

old = '''        @_qqofficial_retry()
        async def _do_upload():
            result = await self.bot.api._http.request(route, json=payload)
            if result is None:
                err_msg = "\u4e0a\u4f20\u6587\u4ef6API\u8fd4\u56deNone\uff0c\u89e6\u53d1\u91cd\u8bd5"
                raise APIReturnNoneError(err_msg)
            return result'''

new = '''        @_qqofficial_retry()
        async def _do_upload():
            try:
                result = await self.bot.api._http.request(route, json=payload)
            except botpy.errors.ServerError as _exc:
                # QQ \u8bed\u97f3(file_type=3) \u6709\u65f6\u957f\u4e0a\u9650\uff0c\u8d85\u51fa\u4f1a\u62a5 40093013
                # \u201c\u4e0a\u4f20\u97f3\u9891\u65f6\u957f\u8d85\u8fc7\u9650\u5236\u201d\u3002\u8fd9\u91cc\u5c31\u5730\u964d\u7ea7\u6210\u666e\u901a\u6587\u4ef6(file_type=4)
                # \u91cd\u4f20\u4e00\u6b21\uff0c\u907f\u514d\u6574\u9996\u6b4c/\u6574\u6bb5\u8bed\u97f3\u76f4\u63a5\u4e22\u6389\u3001\u7528\u6237\u4ec0\u4e48\u90fd\u6536\u4e0d\u5230\u3002
                if payload.get("file_type") == 3 and "40093013" in str(_exc):
                    logger.warning(
                        "\u8bed\u97f3\u8d85\u51fa\u65f6\u957f\u4e0a\u9650(40093013)\uff0c\u81ea\u52a8\u964d\u7ea7\u4e3a\u6587\u4ef6\u4e0a\u4f20: %s", file_source
                    )
                    payload["file_type"] = 4
                    result = await self.bot.api._http.request(route, json=payload)
                else:
                    raise
            if result is None:
                err_msg = "\u4e0a\u4f20\u6587\u4ef6API\u8fd4\u56deNone\uff0c\u89e6\u53d1\u91cd\u8bd5"
                raise APIReturnNoneError(err_msg)
            return result'''

n = s.count(old)
print("occurrences =", n)
if n != 1:
    raise SystemExit("pattern not unique, abort")
open(p, "w", encoding="utf-8").write(s.replace(old, new))
py_compile.compile(p, doraise=True)
print("ADAPTER PATCHED + COMPILED OK")

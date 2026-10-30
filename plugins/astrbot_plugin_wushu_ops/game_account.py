"""Game verification bridge. No LLM, passwords, recovery codes or tokens in replies."""
import re
from urllib.parse import urlparse
import aiohttp


async def verify_game_account(event, config, realm, code):
    expected = str(config.get("game_account_platform_id", ""))
    if not expected or event.get_platform_name() != "qq_official" or event.get_platform_id() != expected:
        return "此机器人通道尚未启用游戏账号验证。"
    if not event.get_group_id():
        return "请在游戏交流群里 @大肥鱼，发送游戏页面生成的绑定指令。"
    if realm not in ("3000", "3001") or not re.fullmatch(r"[A-Fa-f0-9]{16}", code):
        return "请复制游戏里生成的完整指令：绑定游戏 3000或3001 绑定码。"
    sender = str(event.get_sender_id() or "")
    if not sender or len(sender) > 160:
        return "未能读取发送者身份，请稍后重试。"
    base = str(config.get("game_account_url_" + realm, "")).rstrip("/")
    key = str(config.get("game_account_bridge_key", ""))
    parsed = urlparse(base)
    if len(key) < 32 or parsed.scheme not in ("http", "https") or (
        parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost")
    ):
        return "游戏账号验证通道尚未配置完成。"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.post(base + "/api/account/bridge", json={"code": code.upper(), "sender": sender},
                                    headers={"X-Account-Bridge": key}, allow_redirects=False) as response:
                if response.status != 200:
                    return "验证未完成：绑定码可能过期、已使用或服务尚未启用。请回游戏重新生成。"
                result = await response.json()
                if result.get("verified") is not True:
                    return "验证未完成，请稍后重试。"
        return "已验证本次发消息的 QQ 身份，请回刚才的游戏页面确认登录。只验证自己发起的请求，不要替他人验证。"
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return "游戏验证服务暂时无法连接，请稍后重试。没有修改你的成绩。"

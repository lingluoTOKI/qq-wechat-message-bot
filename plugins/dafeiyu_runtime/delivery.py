"""Receipt-checked plain-text delivery for QQ Official. No automatic resend."""
import asyncio
import secrets
from collections.abc import Mapping


async def send_text(context, origin, text, *, timeout=15, reply_msg_id=None, markdown=False):
    parts = str(origin).split(':', 2)
    if len(parts) != 3 or not all(parts) or not str(text).strip():
        raise ValueError('发送目标或内容为空')
    platform_id, kind, recipient = parts
    platform = context.get_platform_inst(platform_id)
    if platform is None:
        raise RuntimeError('平台尚未就绪')
    if platform.meta().name != 'qq_official':
        raise RuntimeError('此发送入口仅支持QQ官方平台，未发送')
    api = platform.client.api
    payload = {'content': str(text), 'msg_type': 0}
    if markdown:
        payload = {'markdown': {'content': str(text)}, 'msg_type': 2}
    # Reuse a valid passive-reply context when available; otherwise let the
    # official API enforce proactive-send permissions. Never invent a msg_id.
    msg_id = reply_msg_id
    if msg_id:
        payload['msg_id'] = msg_id
        payload['msg_seq'] = secrets.randbelow(10000) + 1
    if kind == 'GroupMessage':
        request = api.post_group_message(group_openid=recipient, **payload)
    elif kind == 'FriendMessage':
        request = api.post_c2c_message(openid=recipient, **payload)
    else:
        raise ValueError('不支持的发送会话类型')
    response = await asyncio.wait_for(request, timeout)
    if not isinstance(response, Mapping) or not response.get('id'):
        raise RuntimeError('平台未返回消息回执，送达状态未知')
    return str(response['id'])

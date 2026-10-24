"""按需读取有界的微信历史，不转发原文或自动发送总结。"""
import time
from wechat_listener import Listener


def collect(days,selected=None):
    listener=Listener()
    try:
        listener.connect()
        reader=listener.reader
        cutoff=time.time()-days*86400
        conversations=[]
        truncated=False
        # 仅查最近活跃的10个会话、各50条，避免全库扫描占用资源。
        for chat in ([{'username':user,'last_time':time.time()} for user in selected] if selected is not None else reader.get_sessions(limit=10)):
            user=chat['username']
            if user.startswith(('gh_','weixin','filehelper')) or user==reader.wxid:continue
            if float(chat.get('last_time') or 0)<cutoff:continue
            rows=reader.get_messages(user,limit=50)
            messages=[{'sender':m.get('sender_username') or '未知','text':str(m.get('content') or '')[:500],'time':m.get('create_time')} for m in reversed(rows) if m.get('type')=='文本' and float(m.get('create_time') or 0)>=cutoff and m.get('sender_username')!=reader.wxid]
            if len(rows)==50:truncated=True
            if messages or selected is not None:
                conversations.append({'id':str(len(conversations)),'source':user,'kind':'group' if user.endswith('@chatroom') else 'private','label':reader.get_nickname(user) or user,'messages':messages})
        return conversations,truncated
    finally:
        listener.db.close()

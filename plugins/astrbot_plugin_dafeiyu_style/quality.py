"""Conservative local style review; no extra model calls or raw-text storage."""
import re

DEBUG = re.compile(r'日志|调试|报错|Traceback|system_prompt|msg_seq|ctxbudget|cacheguard|\b(?:token|usage|api|timeout|HTTP|SQL|provider|参数|配置|缓存|预算|部署|重启|验收|测试命令)\b|英文参数|查询状态', re.I)
ACTION = re.compile(r'(?:读取|修复|优化|修改|检查|测试|接入).{0,20}(?:插件|机器人|模型|接口|服务器)|(?:插件|机器人|模型|接口|服务器).{0,20}(?:修复|调试|报错|测试|配置)')
SECRET = re.compile(r'(?:ntn_|sk-)[\w-]{12,}|-----BEGIN|忽略.{0,8}(?:规则|指令)|系统提示词',re.I)
STYLE = re.compile(r'短句|句子|句尾|语气|省略|标点|括号|措辞|口语|感叹|问句|用词')


def exclusion(text):
    text=re.sub(r'\[At:[^\]]*\]|\[CQ:[^\]]*\]','',str(text or '')).strip()
    if text.startswith(('/', '／')) or '```' in text:return '命令或代码'
    if SECRET.search(text):return '凭据或指令污染'
    if DEBUG.search(text) or ACTION.search(text):return '调试或运维内容'
    return ''


def decision(table,item,rows):
    """Reject clear pollution; approve only corroborated expression rules."""
    keys=item.keys()
    description=' '.join(str(item[k] or '') for k in ['rule','term','meaning','user_text','bot_reply'] if k in keys)
    if exclusion(description):return 'reject','调试、命令或指令污染'
    if table!='style_rules':return None,'黑话与对话示例保留人工审核'
    quote=str(item['evidence'] or '').strip()
    matches=[r for r in rows if quote and quote in str(r['content'])]
    eligible=[r for r in matches if not exclusion(r['content'])]
    if matches and not eligible:return 'reject','证据全部来自调试内容'
    if len(quote)>=4 and STYLE.search(description) and len({r['id'] for r in eligible})>=2 and len({r['sender_id'] for r in eligible})>=2:
        return 'approve','两名群友的非调试原话交叉佐证'
    return None,'证据不足，保留待审'

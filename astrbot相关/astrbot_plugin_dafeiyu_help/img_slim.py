"""历史图片瘦身：把「3 轮以前」的历史图片换成一行文字占位。

为什么做这件事：
  图片一旦进了对话历史，就会每轮请求都重发一次。实测某个群里一张 223KB 的
  base64 图，单条消息就占 ~56k token；四个会话合计让每次请求多背 23k~85k token。
  历史上这些图片还会把上下文顶过 deepseek 的 64k 上限，周期性触发框架的
  llm_compress 摘要压缩（每次都要把全量历史重发一遍，前缀缓存还要重建）。
  历史图片本身没有再次"看"的价值：真要看图，用户会在当轮重新发。

不降智的边界：
  · 当前轮和最近 KEEP_USER_TURNS 轮的图片**原样保留**，连着追问图里的细节不受影响；
  · 只处理长度超过 MIN_PAYLOAD 的内嵌大图（data: URI / base64），普通短链接不动；
  · 占位是一段文字，模型仍知道"这里以前有过一张图"，只是不再重传像素。
"""

# 保留最近几轮（按用户消息计）的图片；更早的才省略
KEEP_USER_TURNS = 3

# 小于这个长度的图片地址（普通 http 链接）几乎不占 token，直接放过
MIN_PAYLOAD = 2000

PLACEHOLDER = "[历史图片已省略：这张图在更早的对话里，需要的话重新发一次]"

_IMAGE_TYPES = ("image_url", "image")


def _role_of(msg) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role") or "")
    return str(getattr(msg, "role", "") or "")


def _content_of(msg):
    if isinstance(msg, dict):
        return msg.get("content")
    return getattr(msg, "content", None)


def _payload_of(seg) -> str:
    v = seg.get("image_url") if "image_url" in seg else seg.get("image")
    if isinstance(v, dict):
        v = v.get("url") or v.get("path") or ""
    return v if isinstance(v, str) else ""


def slim_history_images(req) -> tuple[int, int]:
    """就地替换 req.contexts 里超出保留窗口的图片。

    必须在 agent runner 构建之前调用（on_llm_request 钩子内），
    这样 runner 拿到的历史里已经没有大图。

    Returns:
        (省略张数, 省略掉的字符数)
    """
    contexts = getattr(req, "contexts", None)
    if not isinstance(contexts, list) or not contexts:
        return 0, 0

    # 从后往前数用户消息，找到"保留窗口"的左边界
    keep_from = 0
    seen_user = 0
    for i in range(len(contexts) - 1, -1, -1):
        if _role_of(contexts[i]) == "user":
            seen_user += 1
            if seen_user >= KEEP_USER_TURNS:
                keep_from = i
                break

    removed = 0
    chars = 0
    for i in range(keep_from):
        content = _content_of(contexts[i])
        if not isinstance(content, list):
            continue
        for j, seg in enumerate(content):
            if not isinstance(seg, dict) or seg.get("type") not in _IMAGE_TYPES:
                continue
            payload = _payload_of(seg)
            if len(payload) < MIN_PAYLOAD:
                continue
            content[j] = {"type": "text", "text": PLACEHOLDER}
            removed += 1
            chars += len(payload)
    return removed, chars

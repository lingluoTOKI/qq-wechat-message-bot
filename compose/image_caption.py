from astrbot.api.all import *
from typing import Optional
import asyncio

class ImageCaptionUtils:
    """
    图片转述工具类

    用于调用大语言模型将图片转述为文本描述
    """

    # 保存context和config对象的静态变量
    context: Optional[Context] = None
    config: Optional[AstrBotConfig] = None
    # 图片描述缓存
    caption_cache: dict[str, str] = {}
    
    @staticmethod
    def init(context: Context, config: AstrBotConfig):
        """初始化图片转述工具类，保存context和config引用"""
        ImageCaptionUtils.context = context
        ImageCaptionUtils.config = config

    # 预算闸门的状态缓存（避免每条消息都查库）
    _budget_checked_at: float = 0.0
    _budget_blocked: bool = False

    @staticmethod
    def _budget_exhausted(image_processing_config: dict) -> bool:
        """读取「Token成本控制」插件的当日用量，达到阈值时临时关闭图片转述。

        设计意图：识图是最贵的单次调用之一，额度紧张时优先砍掉它，
        额度次日刷新后自动恢复，无需人工干预。
        """
        import os, sqlite3, time
        gate = image_processing_config.get("budget_gate", {}) or {}
        if not gate.get("enabled", True):
            return False

        now = time.time()
        if now - ImageCaptionUtils._budget_checked_at < 60:
            return ImageCaptionUtils._budget_blocked
        ImageCaptionUtils._budget_checked_at = now

        try:
            db = gate.get(
                "db_path",
                "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db",
            )
            if not os.path.exists(db):
                ImageCaptionUtils._budget_blocked = False
                return False

            ratio = float(gate.get("threshold_ratio", 0.8))
            limit = int(gate.get("daily_token_budget", 2200000))

            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            # 以库内最新记录的日期作为「今天」，规避容器时区与本地不一致的问题
            row = conn.execute(
                "SELECT COALESCE(SUM("
                " COALESCE(token_input_other,0) + COALESCE(token_input_cached,0)"
                " + COALESCE(token_output,0) + COALESCE(injection_total,0)), 0) "
                "FROM cost_supplements "
                "WHERE date(created_at) = (SELECT date(MAX(created_at)) FROM cost_supplements)"
            ).fetchone()
            conn.close()

            used = int(row[0] or 0)
            blocked = limit > 0 and used >= limit * ratio
            ImageCaptionUtils._budget_blocked = blocked
            if blocked:
                logger.info(
                    f"[预算闸门] 当日用量 {used}/{limit}（{used / limit:.0%} ≥ {ratio:.0%}），"
                    f"已自动关闭图片转述以节省 token"
                )
            return blocked
        except Exception as _e:
            logger.debug(f"[预算闸门] 读取用量失败，放行识图: {_e}")
            ImageCaptionUtils._budget_blocked = False
            return False

    
    @staticmethod
    async def generate_image_caption(
            image: str, # 图片的base64编码或URL
            umo: Optional[str] = None, # unified_msg_origin，用于 UMO 路由
            timeout: int = 30
        ) -> Optional[str]:
        """
        为单张图片生成文字描述

        Args:
            image: 图片的base64编码或URL
            umo: unified_msg_origin，用于获取对应 UMO 的 provider
            timeout: 超时时间（秒）

        Returns:
            生成的图片描述文本，如果失败则返回None
        """
        # 检查缓存
        if image in ImageCaptionUtils.caption_cache:
            logger.debug(f"命中图片描述缓存: {image[:50]}...")
            return ImageCaptionUtils.caption_cache[image]
            
        # 获取配置
        config = ImageCaptionUtils.config
        context = ImageCaptionUtils.context

        if not config or not context:
            logger.warning("ImageCaptionUtils 未初始化")
            return None

        # 检查是否已启用图片转述
        image_processing_config = config.get("image_processing", {})
        if not image_processing_config.get("use_image_caption", False):
            return None

        # ---- 预算闸门：当日 token 接近/超出预算时自动关闭识图，避免白烧 ----
        if ImageCaptionUtils._budget_exhausted(image_processing_config):
            return None

        provider_id = image_processing_config.get("image_caption_provider_id", "")
        # 获取提供商，支持 UMO 路由
        if provider_id == "":
            provider = context.get_using_provider(umo=umo)
        else:
            provider = context.get_provider_by_id(provider_id)

        if not provider or not hasattr(provider, "text_chat"):
             logger.warning(f"无法找到提供商: {provider_id if provider_id else '默认'}")
             return None

        text_chat = getattr(provider, "text_chat")
        try:
            # 带超时控制的调用大模型进行图片转述
            async def call_llm():
                return await text_chat(
                    prompt=image_processing_config.get("image_caption_prompt", "请直接简短描述这张图片"),
                    contexts=[],
                    image_urls=[image],
                    func_tool=None,
                    system_prompt=""
                )
            
            # 使用asyncio.wait_for添加超时控制
            llm_response = await asyncio.wait_for(call_llm(), timeout=timeout)
            caption = llm_response.completion_text
            
            # 缓存结果
            if caption:
                 ImageCaptionUtils.caption_cache[image] = caption
                 logger.debug(f"缓存图片描述: {image[:50]}... -> {caption}")
                 
            return caption
        except asyncio.TimeoutError:
            logger.warning(f"图片转述超时，超过了{timeout}秒")
            return None
        except Exception as e:
            logger.error(f"图片转述失败: {e}")
            return None

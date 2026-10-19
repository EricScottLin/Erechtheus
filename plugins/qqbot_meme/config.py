"""表情包管理插件配置（前缀 ``qqbot_meme_``）。

示例（.env.prod）::

    qqbot_meme_poke_enabled=true
    qqbot_meme_poke_cooldown=3
    qqbot_meme_max_mb=5
    qqbot_meme_quote_enabled=true
"""

from typing import Literal

from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    """表情包 / 金句库配置。"""

    qqbot_meme_poke_enabled: bool = Field(
        default=True, description="被戳一戳时是否随机回一张表情包",
    )
    qqbot_meme_poke_cooldown: int = Field(
        default=3, ge=0, le=60,
        description="同一群/会话两次戳一戳回图的最小间隔（秒）",
    )
    qqbot_meme_max_mb: int = Field(
        default=5, ge=1, le=20,
        description="表情包上传与随机发送的单文件大小上限（MB）",
    )
    qqbot_meme_quote_enabled: bool = Field(
        default=True, description="是否启用 /金句",
    )
    qqbot_meme_essence_sync: bool = Field(
        default=True, description="是否定时把群精华自动收进金句库",
    )
    qqbot_meme_essence_interval: int = Field(
        default=60, ge=30, le=3600,
        description="群精华轮询间隔（秒）",
    )
    qqbot_meme_poke_image_weight: int = Field(
        default=3, ge=0, le=100,
        description="戳一戳抽到图片（含外部表情库）的权重",
    )
    qqbot_meme_poke_quote_weight: int = Field(
        default=1, ge=0, le=100,
        description="戳一戳抽到金句（文本）的权重",
    )
    qqbot_meme_source_weighting: Literal["count", "uniform"] = Field(
        default="count",
        description="图片/文本来源的加权方式：count=按库大小加权，uniform=每个来源等概率",
    )


plugin_config: Config = get_plugin_config(Config)

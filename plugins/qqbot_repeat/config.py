"""复读插件配置（前缀 ``qqbot_repeat_``）。

两类能力：

- **连续复读**：同一群连续多个不同用户发相同文本/图片时跟发一次
- **我说你说**：教机器人「听到 X 就回 Y」，回复可以是文字，也可以是表情包图片

示例（.env.prod）::

    # 连续复读
    qqbot_repeat_enabled=true
    qqbot_repeat_include_images=true
    qqbot_repeat_times=3
    qqbot_repeat_cooldown=60
    qqbot_repeat_max_len=200

    # 我说你说
    qqbot_repeat_teach_enabled=true
    qqbot_repeat_teach_admin_only=true
    qqbot_repeat_teach_match=exact
    qqbot_repeat_teach_cooldown=3
    qqbot_repeat_teach_max_rules=100
    qqbot_repeat_teach_max_mb=5
"""

import re
from typing import Any, Literal

from nonebot import get_plugin_config
from pydantic import BaseModel, Field, field_validator


def _to_int_set(value: Any) -> Any:
    """把 ``"123,456"`` / ``[123, 456]`` 统一转成 ``set[int]``。"""
    if isinstance(value, str):
        parts = [p for p in re.split(r"[,，\s]+", value.strip()) if p]
        return {int(p) for p in parts}
    if isinstance(value, (list, tuple, set)):
        return {int(v) for v in value}
    return value


class Config(BaseModel):
    """复读 / 我说你说 配置。"""

    # ---- 连续复读 ----
    qqbot_repeat_enabled: bool = Field(
        default=True, description="是否启用连续复读",
    )
    qqbot_repeat_include_images: bool = Field(
        default=True, description="连续复读是否包含图片/表情",
    )
    qqbot_repeat_times: int = Field(
        default=3, ge=2, le=10,
        description="连续多少个不同用户发相同内容后复读",
    )
    qqbot_repeat_cooldown: int = Field(
        default=60, ge=0, le=3600,
        description="同一群两次复读的最小间隔（秒）",
    )
    qqbot_repeat_max_len: int = Field(
        default=200, ge=1, le=2000,
        description="超过该长度的文本不参与复读",
    )

    # ---- 我说你说 ----
    qqbot_repeat_teach_enabled: bool = Field(
        default=True, description="是否启用「我说X你说Y」教学与触发",
    )
    qqbot_repeat_teach_admin_only: bool = Field(
        default=True, description="是否只有群主/群管/超管能教和删",
    )
    qqbot_repeat_teach_match: Literal["exact", "contains"] = Field(
        default="exact",
        description="触发方式：exact=整句完全相同，contains=消息包含触发词",
    )
    qqbot_repeat_teach_cooldown: int = Field(
        default=3, ge=0, le=3600,
        description="同一触发词两次回复的最小间隔（秒）",
    )
    qqbot_repeat_teach_max_rules: int = Field(
        default=100, ge=1, le=1000,
        description="每个群最多保存多少条教学",
    )
    qqbot_repeat_teach_max_trigger_len: int = Field(
        default=32, ge=1, le=100,
        description="触发词最大长度",
    )
    qqbot_repeat_teach_max_reply_len: int = Field(
        default=500, ge=1, le=2000,
        description="文字回复的最大长度",
    )
    qqbot_repeat_teach_max_mb: int = Field(
        default=5, ge=1, le=20,
        description="教学图片的单文件大小上限（MB）",
    )
    qqbot_repeat_extra_admins: set[int] = Field(
        default_factory=set,
        description="额外允许教/删的 QQ 号集合（会与 qqbot_group_extra_admins 合并）",
    )

    @field_validator("qqbot_repeat_extra_admins", mode="before")
    @classmethod
    def _parse_extra_admins(cls, value: Any) -> Any:
        return _to_int_set(value)


plugin_config: Config = get_plugin_config(Config)

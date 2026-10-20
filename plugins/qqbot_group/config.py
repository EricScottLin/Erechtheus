"""群聊管理插件配置（前缀 ``qqbot_group_``）。

NoneBot 的插件配置通过 pydantic 模型声明，配置项从环境变量 / .env / .env.prod
读取，命名规则为 ``qqbot_group_`` 前缀 + 小写字段名。

示例（.env.prod）::

    # /烟 随机禁言时长区间（秒），上限 8 小时
    qqbot_group_smoke_min_seconds=60
    qqbot_group_smoke_max_seconds=28800
    # /禁言 未显式指定时长时的默认禁言（秒）
    qqbot_group_ban_default_seconds=600
    # 额外允许使用群管命令的 QQ 号
    qqbot_group_extra_admins=123456,789012
    # 复读：连续相同文本达到几次触发、冷却时间、最大长度
    qqbot_group_repeat_enabled=true
    qqbot_group_repeat_times=3
    qqbot_group_repeat_cooldown=60
"""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from nonebot import get_plugin_config

# 8 小时，/烟 随机禁言的硬上限（秒）
SMOKE_MAX_SECONDS = 8 * 3600
# OneBot 禁言 API 的时长上限：30 天（秒）
BAN_MAX_SECONDS = 30 * 86400


def _to_int_set(value: Any) -> Any:
    """把 ``"123,456"`` / ``[123, 456]`` 统一转成 ``set[int]``。"""
    if isinstance(value, str):
        parts = [p for p in re.split(r"[,，\s]+", value.strip()) if p]
        return {int(p) for p in parts}
    if isinstance(value, (list, tuple, set)):
        return {int(v) for v in value}
    return value


class Config(BaseModel):
    """群聊管理插件配置。"""

    # ---- 禁言 ----
    qqbot_group_smoke_min_seconds: int = Field(
        default=60, ge=1, le=SMOKE_MAX_SECONDS,
        description="/烟 随机禁言时长下限（秒）",
    )
    qqbot_group_smoke_max_seconds: int = Field(
        default=SMOKE_MAX_SECONDS, ge=1, le=SMOKE_MAX_SECONDS,
        description="/烟 随机禁言时长上限（秒）",
    )
    qqbot_group_ban_default_seconds: int = Field(
        default=600, ge=1, le=BAN_MAX_SECONDS,
        description="/禁言 未指定时长时的默认禁言（秒）",
    )

    # ---- 权限 ----
    qqbot_group_extra_admins: set[int] = Field(
        default_factory=set,
        description="额外允许使用群管命令的 QQ 号集合",
    )

    # ---- 禁言套餐 / 精致睡眠 ----
    qqbot_group_sleep_min_hours: int = Field(
        default=6, ge=1, le=24, description="精致睡眠最小时长（小时）",
    )
    qqbot_group_sleep_max_hours: int = Field(
        default=12, ge=1, le=24, description="精致睡眠最大时长（小时）",
    )
    qqbot_group_package_min_minutes: int = Field(
        default=1, ge=1, le=1440, description="禁言套餐最小时长（分钟）",
    )
    qqbot_group_package_max_minutes: int = Field(
        default=60, ge=1, le=1440, description="禁言套餐最大时长（分钟）",
    )

    # ---- 早安/晚安 ----
    qqbot_group_greeting_reward_enabled: bool = Field(
        default=True, description="早安/晚安是否按睡眠时长发月币",
    )

    # ---- 复读 ----
    qqbot_group_repeat_enabled: bool = Field(
        default=True, description="是否启用复读",
    )
    qqbot_group_repeat_include_images: bool = Field(
        default=True, description="复读是否包含图片/表情",
    )
    qqbot_group_repeat_times: int = Field(
        default=3, ge=2, le=10,
        description="连续出现多少次相同文本后复读",
    )
    qqbot_group_repeat_cooldown: int = Field(
        default=60, ge=0, le=3600,
        description="同一群两次复读的最小间隔（秒）",
    )
    qqbot_group_repeat_max_len: int = Field(
        default=200, ge=1, le=2000,
        description="超过该长度的消息不参与复读",
    )

    @field_validator("qqbot_group_extra_admins", mode="before")
    @classmethod
    def _parse_extra_admins(cls, value: Any) -> Any:
        return _to_int_set(value)

    @model_validator(mode="after")
    def _check_ranges(self) -> "Config":
        if self.qqbot_group_smoke_min_seconds > self.qqbot_group_smoke_max_seconds:
            raise ValueError(
                "qqbot_group_smoke_min_seconds 不能大于 qqbot_group_smoke_max_seconds"
            )
        if self.qqbot_group_sleep_min_hours > self.qqbot_group_sleep_max_hours:
            raise ValueError("qqbot_group_sleep_min_hours 不能大于 sleep_max_hours")
        if self.qqbot_group_package_min_minutes > self.qqbot_group_package_max_minutes:
            raise ValueError(
                "qqbot_group_package_min_minutes 不能大于 package_max_minutes"
            )
        return self


plugin_config: Config = get_plugin_config(Config)

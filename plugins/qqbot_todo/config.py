"""待办插件配置。

配置项通过环境变量 / .env / .env.prod 注入，前缀 ``qqbot_todo_``。

示例（.env.prod）::

    # 群聊总闸（还要本群 /开启 待办 才生效）；true = 允许群里用
    qqbot_todo_group_enabled=true
    # 单条待办提醒
    qqbot_todo_remind_enabled=true
    # 每天早上 9 点把待办私聊推给超级用户
    qqbot_todo_daily_enabled=true
    qqbot_todo_daily_time=09:00
    # 交给 DSH 的交接文件放哪（宿主机绝对路径；留空 = 不写文件）
    qqbot_todo_dsh_dir=/srv/qqbot/data/qqbot_todo/dsh
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    """QQBot 待办插件配置。"""

    qqbot_todo_group_enabled: bool = Field(
        default=True,
        description="群聊总闸：true 时再按群开关控制（/开启 待办）；false 则群里一律不可用",
    )
    qqbot_todo_remind_enabled: bool = Field(
        default=True, description="单条待办到点是否私聊提醒"
    )
    qqbot_todo_daily_enabled: bool = Field(
        default=False, description="是否每天定时把未完成的待办私聊推给超级用户"
    )
    qqbot_todo_daily_time: str = Field(
        default="09:00", description="每日推送时间 HH:MM（服务器时区）"
    )
    qqbot_todo_dsh_dir: str = Field(
        default="",
        description="交给 DSH 的交接文件输出目录（留空则不写文件，只发消息）",
    )
    qqbot_todo_max_active: int = Field(
        default=200, ge=1, description="未完成待办上限，超出拒绝新增"
    )
    qqbot_todo_title_max: int = Field(
        default=200, ge=10, le=2000, description="单条待办标题最大长度"
    )

    @field_validator("qqbot_todo_daily_time", mode="before")
    @classmethod
    def _check_daily_time(cls, value: Any) -> Any:
        hour, minute = split_hhmm(str(value))
        if hour is None:
            raise ValueError("qqbot_todo_daily_time 需要 HH:MM 格式，例如 09:00")
        return f"{hour:02d}:{minute:02d}"


def split_hhmm(value: str) -> tuple[int | None, int]:
    """``"9:30"`` -> ``(9, 30)``；非法返回 ``(None, 0)``。"""
    text = (value or "").strip()
    if text.endswith("点"):
        text = text[:-1] + ":00"
    parts = text.replace("：", ":").split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        return None, 0
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, 0
    return hour, minute

"""@命令修复插件配置。"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """qqbot_atcmd_fix 配置。

    配置项通过环境变量或 .env / .env.prod 注入，前缀 `qqbot_atcmd_fix_`。
    """

    qqbot_atcmd_fix_enabled: bool = Field(
        default=True,
        description="是否启用「@机器人+命令」修复（摘掉指向机器人的 at 段，使命令能正常解析）",
    )

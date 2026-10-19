"""插件配置。

NoneBot 的插件配置通过 pydantic 模型声明，配置项从环境变量 / .env 读取，
命名规则为 `qqbot_toolbox_` 前缀 + 小写字段名。
"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """QQBot 工具箱插件配置。"""

    # 回显 / 帮助等回复是否 @ 发起者（群聊里更易辨认）
    qqbot_toolbox_at_sender: bool = Field(default=True, description="回复时是否 @ 发起者")

    # /help 输出最多列出的插件条数，防止刷屏
    qqbot_toolbox_help_max: int = Field(default=20, ge=1, le=100, description="/help 最多列出的插件数")

    # /status 调用 OneBot 接口的超时时间（秒）
    qqbot_toolbox_status_timeout: float = Field(default=5.0, gt=0, description="/status 接口超时（秒）")

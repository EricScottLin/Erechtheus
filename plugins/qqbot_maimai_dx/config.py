"""舞萌 DX 直连插件配置。

配置项通过 .env / .env.prod 注入，前缀 `maimai_dx_`（NoneBot 的 get_plugin_config
会自动去掉插件名前缀，这里统一用 maimai_dx_ 以免与 nonebot_plugin_maimaidx 冲突）。

所有项默认偏安全：demo 模式、只允许私聊、不实传查分器、白名单为空（仅 bot 主可用）。
"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """舞萌直连（MaimaiDXCHN）配置。"""

    maimai_dx_mode: str = Field(
        default="demo",
        description="demo=完全本地模拟，不请求游戏服/查分器；real=真实读取（需要先私聊绑定二维码）",
    )
    maimai_dx_allow: list[str] = Field(
        default_factory=list,
        description="允许使用舞萌直连的 QQ 号；bot 主始终允许，列表为空时只有 bot 主可用",
    )
    maimai_dx_private_only: bool = Field(
        default=True, description="只处理私聊消息（二维码与成绩都属于账号数据）"
    )
    maimai_dx_provider: str = Field(
        default="lxns", description="成绩同步目标：lxns 落雪 / diving-fish 水鱼"
    )
    maimai_dx_prober_token: str = Field(
        default="", description="查分器凭证；只从环境变量注入，绝不通过聊天命令输入"
    )
    maimai_dx_allow_upload: bool = Field(
        default=False, description="是否允许把成绩实传到查分器（仍需用户发送「舞萌上传」确认）"
    )
    maimai_dx_version: int = Field(default=156, ge=100, description="游戏协议版本")
    maimai_dx_min_session_seconds: float = Field(
        default=65, ge=0, description="真实读取时登录后至少保留的秒数（服务端会话时序要求）"
    )
    maimai_dx_keychip_id: str = Field(default="", description="机台 Keychip ID；留空用包内默认值")
    maimai_dx_client_id: str = Field(default="", description="机台 Client ID；留空用包内默认值")
    maimai_dx_region_id: int = Field(default=1, ge=1, description="Region ID")
    maimai_dx_place_id: int = Field(default=1403, ge=1, description="Place ID")
    maimai_dx_identity_salt: str = Field(
        default="",
        description="内部身份加盐；设置后 QQ 号不进内存键。改动会让所有已绑定会话失效",
    )

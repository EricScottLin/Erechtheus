"""舞萌 DX 直连插件配置。

配置项通过 .env / .env.prod 注入，前缀 `maimai_dx_`（NoneBot 的 get_plugin_config
会自动去掉插件名，这里统一加前缀，避免和 nonebot_plugin_maimaidx 撞名）。

默认值刻意偏安全：demo 模式、只允许一个群、只允许私聊绑定、不实传查分器。
"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """舞萌直连（MaimaiDXCHN）配置。"""

    maimai_dx_mode: str = Field(
        default="demo",
        description="demo=完全本地模拟，不请求游戏服/查分器；real=真实读取（需先私聊绑定二维码）",
    )
    maimai_dx_group_ids: list[int] = Field(
        default_factory=lambda: [984616276],
        description="允许使用舞萌直连的群号；其他群完全静默，不回复任何内容",
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

    # ---------- 收费与门槛 ----------
    maimai_dx_prices: dict[str, int] = Field(
        default_factory=dict,
        description="覆盖默认价格（月币），例如 {\"舞萌成绩\": 8}；键是指令名",
    )
    maimai_dx_bind_min_level: int = Field(
        default=10, ge=1, description="绑定所需的荣耀等级（10 级 = 累计 900 荣耀，且要先 /升阶）"
    )
    maimai_dx_self_unbind_level: int = Field(
        default=9999, ge=1, description="允许自助解绑的荣耀等级；默认满级，实际等价于仅管理员"
    )
    maimai_dx_qr_max_bytes: int = Field(
        default=4_000_000, ge=10_000, description="二维码图片最大字节数，超过直接跳过"
    )

"""QQBot 核心插件配置。

配置项通过环境变量或 .env / .env.prod 注入，前缀 `qqbot_core_`。
运行时开关（活动开关等）存 settings.json，由 bot 主私聊修改，优先级高于这里。
"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """货币与个人信息基础服务配置。"""

    qqbot_core_at_sender: bool = Field(
        default=True, description="回复时是否 @ 发起者"
    )
    qqbot_core_nickname_max: int = Field(
        default=20, ge=1, description="自设昵称最大长度"
    )
    qqbot_core_signature_max: int = Field(
        default=60, ge=1, description="签名最大长度"
    )

    qqbot_core_discount_per_level: float = Field(
        default=0.005, ge=0.0, le=0.2, description="每级荣耀等级带来的折扣（0.005 = 0.5%，满级约 30%）"
    )
    qqbot_core_discount_cap: float = Field(
        default=0.30, ge=0.0, le=0.9, description="折扣总上限（0.30 = 最多 7 折）"
    )

    qqbot_core_whitelist: list[str] = Field(
        default_factory=list,
        description="可执行 /爆金币 的白名单 QQ 号（bot 主之外）",
    )

    qqbot_core_daily_earn_cap: dict[str, int] = Field(
        default_factory=lambda: {"kleos": 680, "drachma": 5000},
        description="每个代币的日获取上限，防刷币",
    )

    # ---------- /领金币 限时活动 ----------
    qqbot_core_claim_enabled: bool = Field(
        default=False,
        description="活动默认是否开放（bot 主可用 /领金币开关 覆盖）",
    )
    qqbot_core_claim_amount: int = Field(
        default=100, ge=0, description="群等级不到最低档时的保底发放额"
    )
    qqbot_core_claim_tiers: dict[int, int] = Field(
        default_factory=lambda: {1: 100, 20: 300, 50: 600, 100: 1000},
        description="群等级 → 发放额档位（取不超过等级的最高档）",
    )
    qqbot_core_claim_multi_group_bonus: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="每多一个共同群的额外加成（0.10 = +10%）",
    )
    qqbot_core_claim_multi_group_bonus_cap: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="多群加成的总上限（0.30 = 最多 +30%）",
    )

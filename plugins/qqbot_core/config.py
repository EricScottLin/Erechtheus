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
        default=0.0001, ge=0.0, le=0.01, description="每级荣誉等级带来的折扣（0.0001 = 0.01%，Lv.9999 约 100%）"
    )
    qqbot_core_discount_cap: float = Field(
        default=1.0, ge=0.0, le=1.0, description="折扣总上限（1.0 = 满级全免）"
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
        default_factory=lambda: {
            1: 100,
            20: 300,
            50: 600,
            100: 1000,
            150: 1200,
            200: 1400,
            300: 1800,
            400: 2100,
            500: 2400,
        },
        description="累计群等级 → 发放额档位（取不超过累计等级的最高档）",
    )
    qqbot_core_claim_max_groups: int = Field(
        default=5,
        ge=1,
        le=20,
        description="最多累计几个群的等级（群按等级从高到低取）",
    )

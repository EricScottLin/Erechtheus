"""Erectheus 经济系统配置 —— 双代币体系。

代币设计（刻意分离，避免通胀）：
  Kleos「荣耀」    每日签到获得，不可消费，累计决定声望等级。日活 faucet，天然稀缺。
  Drachma「德拉克马」捕鱼获得，用于商店/鱼竿/鱼饵/抽奖。游戏内货币。

两者**不可互相兑换**。原因：捕鱼有 60s 冷却，理论产能约 3600 Drachma/小时，
而签到一天仅一次（100~680 Kleos），产能差两个数量级。若合并成单一货币，
要么签到收益被捕鱼彻底稀释变得无意义，要么为凸显签到而抬高数值导致全网通胀。

配置项通过环境变量或 .env / .env.prod 注入，前缀 `qqbot_economy_`。
"""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """Erectheus 经济系统配置。"""

    # ---------- Kleos 荣耀（签到，不可消费）----------
    qqbot_economy_sign_base: int = Field(default=100, ge=0, description="签到基础荣耀")
    qqbot_economy_sign_streak_bonus: int = Field(
        default=20, ge=0, description="连续签到每天额外荣耀"
    )
    qqbot_economy_sign_streak_max: int = Field(
        default=30, ge=1, description="连签奖励计算的最大天数"
    )
    qqbot_economy_sign_drachma_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="签到附带月币比例（本次荣耀 × 比例，0 = 不给月币）",
    )
    qqbot_economy_kleos_per_level: int = Field(
        default=1000, ge=1, description="每升一级声望所需累计荣耀"
    )

    # ---------- Drachma 德拉克马（捕鱼产出）----------
    qqbot_economy_fish_cooldown: int = Field(
        default=60, ge=0, description="捕鱼冷却时间（秒）"
    )
    qqbot_economy_fish_exp_bonus: int = Field(
        default=0, ge=0, description="捕鱼获得经验的额外加成"
    )

    # ---------- 商店（Drachma 消耗口）----------
    qqbot_economy_bait_price: int = Field(default=10, ge=1, description="鱼饵单价")
    qqbot_economy_bait_bonus: float = Field(
        default=0.5, ge=0, description="使用鱼饵后稀有度权重提升比例"
    )
    qqbot_economy_rod_base_price: int = Field(
        default=500, ge=1, description="鱼竿升到下一级的基础价格"
    )
    qqbot_economy_rod_price_step: int = Field(
        default=300, ge=0, description="鱼竿每升一级递增的价格"
    )
    qqbot_economy_rod_max_level: int = Field(default=10, ge=1, description="鱼竿最高等级")

    # ---------- 抽奖（Drachma 大消耗口，对齐 Chiyuki 量级）----------
    qqbot_economy_gacha_single: int = Field(default=100, ge=1, description="单抽价格")
    qqbot_economy_gacha_ten: int = Field(default=1000, ge=1, description="十连价格")
    qqbot_economy_gacha_hundred: int = Field(default=10000, ge=1, description="百连价格")

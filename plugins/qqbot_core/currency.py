"""Erectheus 货币系统 —— 代码侧唯一事实来源。

本模块是 docs/CURRENCY_SPEC.md 的可执行版本。**所有代币、产出、回收、兑换规则
必须在此登记**，不允许在命令处理函数里硬编码数值。

命名约定（重要）：
  key     代码内部代号，只出现在代码里（kleos / drachma）
  name    对玩家展示的中文名（荣耀 / 月币），所有消息、fmt()、命令一律用它

设计约束（对应规范条目）：
  R1  代币集合封闭，层数 ≤ 3
  R2  同层唯一主要代币
  R3  任何 faucet 必须有产能上限
  R4  日活型 faucet 产能显著低于技能型
  R5  可消费代币 ≥3 个 sink，覆盖高低频
  R7/R8 兑换单向 + 税率 ≥20% + 日限（当前未开放任何通道）
  R9  调整产出必须同步评估回收
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 分层
LAYER_GAME = 0  # 玩法代币：只在单一玩法内循环，不进主经济
LAYER_HARD = 1  # 高级代币：稀缺、不可农
LAYER_SOFT = 2  # 软通货：主要交易媒介
LAYER_PRESTIGE = 3  # 荣誉代币：日活、不可消费

LAYER_NAMES = {
    LAYER_GAME: "玩法代币",
    LAYER_HARD: "高级代币",
    LAYER_SOFT: "软通货",
    LAYER_PRESTIGE: "荣誉代币",
}


@dataclass(frozen=True)
class CurrencySpec:
    """单个代币的完整声明。"""

    key: str  # 代码内部代号
    layer: int
    name: str  # 玩家看到的中文名
    symbol: str
    consumable: bool
    faucet: str  # 产出方式描述
    faucet_cap: str  # 产能上限描述
    sinks: tuple[str, ...] = ()  # 消耗口（R5：可消费代币须 ≥3）


# ---------------------------------------------------------------- 代币登记表
# R1/R2：封闭集合，同层唯一。新增代币必须先过 docs/CURRENCY_SPEC.md §7 检查清单。
CURRENCIES: dict[str, CurrencySpec] = {
    "kleos": CurrencySpec(
        key="kleos",
        layer=LAYER_PRESTIGE,
        name="荣耀",
        symbol="🏛",
        consumable=False,  # L3 不可消费，只累积
        faucet="每日签到（1 次/天）+ 连签加成",
        faucet_cap="1 次/天，单次 100 + 20×(连签−1)，连签封顶 30 天",
        sinks=(),  # R5 不适用于 L3
    ),
    "drachma": CurrencySpec(
        key="drachma",
        layer=LAYER_SOFT,
        name="月币",
        symbol="🌙",
        consumable=True,
        faucet="每日签到（随机 10~60）+ 限时活动 /领金币",
        faucet_cap="签到 1 次/天；活动每人一次",
        sinks=("科技 bot 操作", "限定商品", "后续商店"),  # R5：≥3（规划中）
    ),
    "fishcoin": CurrencySpec(
        key="fishcoin",
        layer=LAYER_GAME,
        name="鱼币",
        symbol="🐟",
        consumable=True,
        faucet="捕鱼（60s 冷却）",
        faucet_cap="1440 次/天；R 10~150 · SR 50~400 · SSR 450~999",
        sinks=("鱼饵", "鱼竿升级", "抽奖"),  # R5：≥3 且覆盖高低频
    ),
}


def get_currency(key: str) -> CurrencySpec:
    try:
        return CURRENCIES[key]
    except KeyError:
        raise KeyError(
            f"未登记的代币 {key!r}；请先在 qqbot_core/currency.py 的 CURRENCIES 中登记，"
            f"并通过 docs/CURRENCY_SPEC.md §7 检查清单"
        ) from None


def fmt(key: str, amount: int) -> str:
    """格式化代币数量，如 '🌙 1,200 月币'。"""
    spec = get_currency(key)
    return f"{spec.symbol} {amount:,} {spec.name}"


# ---------------------------------------------------------------- 兑换规则
# R7/R8：单向 + 税率 ≥20% + 日限。当前**未开放任何通道**（见规范 §5）。
@dataclass(frozen=True)
class ExchangeRule:
    from_key: str
    to_key: str
    rate: float  # 1 from = rate to
    tax: float  # 0~1，≥0.20
    daily_limit: int


EXCHANGE_RULES: tuple[ExchangeRule, ...] = ()  # 预留，刻意为空


def find_exchange(from_key: str, to_key: str) -> ExchangeRule | None:
    for r in EXCHANGE_RULES:
        if r.from_key == from_key and r.to_key == to_key:
            return r
    return None


# ---------------------------------------------------------------- 数值算法
# 集中放在这里，调数值只改这一处，并同步复核规范 §6 的三个指标。

# --- 荣耀（kleos）：签到 faucet（R3 日限 1 次；R4 产能远低于捕鱼）---
SIGN_BASE = 100
SIGN_STREAK_BONUS = 20
SIGN_STREAK_MAX = 30  # 连签奖励封顶天数
KLEOS_PER_LEVEL = 1000  # 声望等级所需累计荣耀


def kleos_for_sign_in(streak: int) -> int:
    """连签天数 → 本次签到获得的荣耀。streak 从 1 开始。"""
    capped = max(1, min(streak, SIGN_STREAK_MAX))
    return SIGN_BASE + SIGN_STREAK_BONUS * (capped - 1)


# --- 荣耀：捕鱼活跃奖励（按稀有度，日上限在配置里）---
FISH_KLEOS_BY_RARITY = {"R": 2, "SR": 5, "SSR": 10}


def kleos_for_fish(rarity: str) -> int:
    """捕到某稀有度鱼时额外获得的荣耀（未登记稀有度返回 0）。"""
    return FISH_KLEOS_BY_RARITY.get(rarity, 0)


def prestige_level(total_kleos: int) -> int:
    """累计荣耀（里程）→ 声望等级（10 荣耀 = 1 级，满级 9999）。"""
    return kleos_progress(total_kleos)[0]


# --- 荣耀里程与觉醒（参考 maimai 旅行伙伴：9/49/299/499/999 各觉醒一次，9999 点亮第六星）---
MILEAGE_PER_LEVEL = 10  # 10 荣耀 = 1 级
MAX_LEVEL = 9999
# (觉醒等级, 称号, emoji)
AWAKENINGS: tuple[tuple[int, str, str], ...] = (
    (9, "月桂", "🌿"),
    (49, "执政官", "⚖️"),
    (299, "大执政官", "🏅"),
    (499, "海神眷者", "🔱"),
    (999, "雅典守护者", "🛡"),
    (9999, "神话", "✨"),
)
STAR_LEVELS: tuple[int, ...] = tuple(item[0] for item in AWAKENINGS)  # 6 颗星
TITLES = AWAKENINGS  # 兼容旧名


def kleos_progress(total: int) -> tuple[int, int]:
    """累计荣耀（里程）→ (等级, 当前等级内已累计里程)。10 荣耀 = 1 级。"""
    total = max(0, int(total))
    level = min(total // MILEAGE_PER_LEVEL + 1, MAX_LEVEL)
    return level, total % MILEAGE_PER_LEVEL


def level_mileage(level: int) -> int:
    """从 level 升到 level+1 需要的里程。满级返回 0。"""
    return 0 if level >= MAX_LEVEL else MILEAGE_PER_LEVEL


def cumulative_mileage(level: int) -> int:
    """达到某等级所需的总里程。"""
    return (min(max(1, level), MAX_LEVEL) - 1) * MILEAGE_PER_LEVEL


def stars_for_level(level: int) -> int:
    """等级对应的星级（每过一次觉醒 +1，最高 6 星）。"""
    return sum(1 for threshold in STAR_LEVELS if level >= threshold)


def title_for_level(level: int) -> tuple[int, str, str] | None:
    """当前等级对应的称号 (觉醒等级, 名称, emoji)；未觉醒返回 None。"""
    best = None
    for threshold, name, emoji in AWAKENINGS:
        if level >= threshold:
            best = (threshold, name, emoji)
    return best


def next_title(level: int) -> tuple[int, str, str] | None:
    """下一个待解锁的称号；已全部觉醒返回 None。"""
    for item in AWAKENINGS:
        if level < item[0]:
            return item
    return None


# --- 月币（drachma）：鱼竿升级 sink（一次性，R6）---
ROD_BASE_PRICE = 500
ROD_PRICE_STEP = 300
ROD_MAX_LEVEL = 10


def rod_upgrade_price(current_level: int) -> int:
    """从 current_level 升到下一级的价格。已达上限返回 0。"""
    if current_level >= ROD_MAX_LEVEL:
        return 0
    return ROD_BASE_PRICE + ROD_PRICE_STEP * (current_level - 1)


def rod_max_total_cost() -> int:
    """L1 → L10 总成本，用于核算 R5/R6 与规范 §6 指标。"""
    return sum(rod_upgrade_price(lv) for lv in range(1, ROD_MAX_LEVEL))


# --- 月币（drachma）：抽奖 sink（高价值低频，负期望，见规范 §6 缓解措施 #1）---
GACHA_SINGLE = 100
GACHA_TEN = 1000
GACHA_HUNDRED = 10000


# ---------------------------------------------------------------- 监控指标
# 规范 §6：M_d / S_d / ρ。靠用户钱包里的累计字段现算，不引入额外存储。
@dataclass
class EconomyMetrics:
    total_drachma_minted: int = 0
    total_drachma_spent: int = 0
    total_kleos_minted: int = 0
    total_fishcoin_minted: int = 0
    total_fishcoin_spent: int = 0
    active_users: int = 0
    drachma_in_circulation: int = 0
    fishcoin_in_circulation: int = 0
    notes: list[str] = field(default_factory=list)


def compute_metrics(users: dict[str, dict]) -> EconomyMetrics:
    """由全体用户数据计算经济指标。

    users: {uid: user_dict}，user_dict 为扁平结构
    （total_*_earned / total_*_spent / <currency>），
    与 qqbot_economy 适配层输出的 all_users() 一致。
    """
    m = EconomyMetrics(active_users=len(users))
    for u in users.values():
        m.total_drachma_minted += int(u.get("total_drachma_earned", 0))
        m.total_drachma_spent += int(u.get("total_drachma_spent", 0))
        m.total_kleos_minted += int(u.get("total_kleos_earned", 0))
        m.total_fishcoin_minted += int(u.get("total_fishcoin_earned", 0))
        m.total_fishcoin_spent += int(u.get("total_fishcoin_spent", 0))
        m.drachma_in_circulation += int(u.get("drachma", 0))
        m.fishcoin_in_circulation += int(u.get("fishcoin", 0))

    if m.total_drachma_minted > 0:
        burn_ratio = m.total_drachma_spent / m.total_drachma_minted
        m.notes.append(f"历史销毁比 S/M = {burn_ratio:.1%}（规范要求 ≥50%）")
        m.notes.append(
            f"ρ(存量/累计铸造) = {m.drachma_in_circulation / m.total_drachma_minted:.2f}"
            "（规范健康区间 3~30 天，需按日铸造折算）"
        )
    return m


# ---------------------------------------------------------------- 自检
def validate_registry() -> list[str]:
    """启动时自检，违反规范即刻暴露。返回问题列表，空列表表示通过。"""
    problems: list[str] = []

    # R1：主经济层数不超 3（LAYER_GAME 玩法代币不计入）
    layers = {c.layer for c in CURRENCIES.values() if c.layer >= LAYER_HARD}
    if len(layers) > 3:
        problems.append(f"R1 违反：层数 {len(layers)} > 3")

    # R2：同层唯一主要代币
    seen: dict[int, list[str]] = {}
    for c in CURRENCIES.values():
        seen.setdefault(c.layer, []).append(c.key)
    for layer, keys in seen.items():
        if layer >= LAYER_HARD and len(keys) > 1:
            problems.append(f"R2 违反：L{layer} 存在多个代币 {keys}")

    # R5：可消费代币须 ≥3 sink
    for c in CURRENCIES.values():
        if c.consumable and len(c.sinks) < 3:
            problems.append(f"R5 违反：{c.key} 可消费但 sink 不足 3 个（{len(c.sinks)}）")

    # R7/R8：兑换规则必须单向且税率 ≥20%
    for r in EXCHANGE_RULES:
        if get_currency(r.from_key).layer <= get_currency(r.to_key).layer:
            problems.append(f"R7 违反：兑换 {r.from_key}->{r.to_key} 非单向降层")
        if r.tax < 0.20:
            problems.append(f"R8 违反：兑换 {r.from_key}->{r.to_key} 税率 {r.tax} < 20%")

    return problems

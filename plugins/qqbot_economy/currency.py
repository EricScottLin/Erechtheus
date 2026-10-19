"""兼容层：货币登记表已迁移到 qqbot_core.currency（唯一事实来源）。

本模块不再重复定义代币与数值，只把 qqbot_core.currency 的公共名字转发出来，
保证 qqbot_economy 内 `from . import currency as cur` 的既有用法不变。
"""

from nonebot.plugin import require

_cur = require("qqbot_core").currency

# 分层
LAYER_HARD = _cur.LAYER_HARD
LAYER_SOFT = _cur.LAYER_SOFT
LAYER_PRESTIGE = _cur.LAYER_PRESTIGE
LAYER_NAMES = _cur.LAYER_NAMES

# 登记表
CurrencySpec = _cur.CurrencySpec
CURRENCIES = _cur.CURRENCIES
get_currency = _cur.get_currency
fmt = _cur.fmt

# 兑换规则
ExchangeRule = _cur.ExchangeRule
EXCHANGE_RULES = _cur.EXCHANGE_RULES
find_exchange = _cur.find_exchange

# 数值算法
SIGN_BASE = _cur.SIGN_BASE
SIGN_STREAK_BONUS = _cur.SIGN_STREAK_BONUS
SIGN_STREAK_MAX = _cur.SIGN_STREAK_MAX
KLEOS_PER_LEVEL = _cur.KLEOS_PER_LEVEL
kleos_for_sign_in = _cur.kleos_for_sign_in
FISH_KLEOS_BY_RARITY = _cur.FISH_KLEOS_BY_RARITY
kleos_for_fish = _cur.kleos_for_fish
prestige_level = _cur.prestige_level
ROD_BASE_PRICE = _cur.ROD_BASE_PRICE
ROD_PRICE_STEP = _cur.ROD_PRICE_STEP
ROD_MAX_LEVEL = _cur.ROD_MAX_LEVEL
rod_upgrade_price = _cur.rod_upgrade_price
rod_max_total_cost = _cur.rod_max_total_cost
GACHA_SINGLE = _cur.GACHA_SINGLE
GACHA_TEN = _cur.GACHA_TEN
GACHA_HUNDRED = _cur.GACHA_HUNDRED

# 监控与自检
EconomyMetrics = _cur.EconomyMetrics
compute_metrics = _cur.compute_metrics
validate_registry = _cur.validate_registry

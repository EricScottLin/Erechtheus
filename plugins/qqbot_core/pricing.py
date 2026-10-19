"""荣耀等级权益：称号 + 折扣。

其他插件（如科技 bot）给功能定价时调用 price()：
    core = require("qqbot_core")
    cost = await core.pricing.price(uid, 100)   # 按荣耀等级打折
    await core.wallet.spend(uid, "drachma", cost)
"""

from nonebot import get_plugin_config

from . import awaken, storage
from .config import Config
from .currency import next_title, prestige_level, title_for_level


async def level_of(user_id: str) -> int:
    return (await awaken.status(user_id))["level"]


async def title_of(user_id: str) -> str:
    """当前称号文案，如 '🌿 月桂'；无称号返回空串。"""
    t = title_for_level(await level_of(user_id))
    return f"{t[2]} {t[1]}" if t else ""


async def discount(user_id: str) -> float:
    """当前折扣（0~cap），例如 0.05 表示 -5%。"""
    cfg = get_plugin_config(Config)
    level = await level_of(user_id)
    return min(max(level - 1, 0) * cfg.qqbot_core_discount_per_level, cfg.qqbot_core_discount_cap)


async def price(user_id: str, base: int) -> int:
    """按荣耀等级折扣后的价格（最低 1）。"""
    rate = await discount(user_id)
    return max(1, int(round(base * (1 - rate))))

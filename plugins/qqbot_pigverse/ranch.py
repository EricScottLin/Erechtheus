"""Phase 4：小猪牧场（Drachma sink）。

喂食消耗 Drachma，提升小猪的「好感等级」（本插件独立记录，不写 rollpig 账本）。
每只猪每日限喂 1 次，价格见 FEED_PRICE。
"""

from __future__ import annotations

from datetime import date

from . import store as pigstore

FEED_PRICE = 50  # Drachma / 次
FEED_DAILY_PER_PIG = 1  # 每只猪每日限喂次数
FED_PER_LEVEL = 5  # 每喂 5 次好感 +1 级


def feed_level(fed: int) -> int:
    return max(0, int(fed)) // FED_PER_LEVEL + 1


def _economy():
    """跨插件访问 economy 数据层（sys.modules，避免 NoneBot 拦截 plugins.* import）。"""
    import sys

    mod = sys.modules.get("plugins.qqbot_economy.data")
    if mod is None:
        raise RuntimeError("qqbot_economy 未加载")
    return mod


async def drachma_balance(uid: str) -> int:
    u = await _economy().get_user(uid)
    return int(u.get("drachma", 0))


async def spend_drachma(uid: str, amount: int) -> bool:
    r = await _economy().spend(uid, "drachma", amount)
    return r is not None


async def feed(uid: str, pig_id: str) -> dict:
    """喂一次：先检查每日限制，再扣 Drachma，最后记录。"""
    ranch = await pigstore.get_ranch(uid)
    rec = ranch.get(pig_id, {})
    today = date.today().isoformat()
    if rec.get("last_fed") == today:
        return {"ok": False, "reason": "today", "fed": int(rec.get("fed", 0))}
    if not await spend_drachma(uid, FEED_PRICE):
        return {"ok": False, "reason": "poor", "fed": int(rec.get("fed", 0))}
    fed = int(rec.get("fed", 0)) + 1
    await pigstore.update_ranch(uid, pig_id, fed=fed, last_fed=today)
    return {"ok": True, "fed": fed}

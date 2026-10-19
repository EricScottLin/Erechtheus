"""货币操作接口 —— 其他插件通过它发币/扣币/转账。

设计要点：
  * 所有写入都经过 storage.transact（单锁串行），不会出现半更新
  * earn 带日上限（防刷），spend 余额不足返回 False，transfer 原子完成
  * 只允许已登记代币（currency.CURRENCIES），禁止插件自造货币
"""

from typing import Any

from . import storage
from .currency import get_currency


def _apply_earn(
    user: dict[str, Any], currency: str, amount: int, day_cap: int | None
) -> int:
    """给单个用户加币，返回实际发放额（受日上限截断）。纯函数，调用方负责持锁。"""
    wallet = user["wallet"][currency]
    if day_cap is None:
        wallet["balance"] += amount
        wallet["total_earned"] += amount
        return amount

    day = storage.today_str()
    daily = wallet.setdefault("daily_earned", {})
    used = int(daily.get(day, 0))
    granted = max(0, min(amount, day_cap - used))
    if granted <= 0:
        return 0
    daily[day] = used + granted
    wallet["balance"] += granted
    wallet["total_earned"] += granted
    return granted


def _apply_spend(user: dict[str, Any], currency: str, amount: int) -> bool:
    """给单个用户扣币，余额不足返回 False。纯函数，调用方负责持锁。"""
    wallet = user["wallet"][currency]
    if wallet["balance"] < amount:
        return False
    wallet["balance"] -= amount
    wallet["total_spent"] += amount
    return True


async def balance(user_id: str, currency: str) -> int:
    get_currency(currency)
    user = await storage.get_user(user_id)
    return int(user["wallet"][currency]["balance"])


async def balances(user_id: str) -> dict[str, int]:
    user = await storage.get_user(user_id)
    return {k: int(v["balance"]) for k, v in user["wallet"].items()}


async def earn(
    user_id: str,
    currency: str,
    amount: int,
    source: str = "",
    day_cap: int | None = None,
) -> int:
    """发币，返回实际发放额。amount ≤ 0 或代币未登记时抛 ValueError。"""
    if amount <= 0:
        raise ValueError("earn amount 必须为正")
    get_currency(currency)

    def _m(user: dict[str, Any]) -> int:
        return _apply_earn(user, currency, amount, day_cap)

    return await storage.transact(user_id, _m)


async def spend(
    user_id: str, currency: str, amount: int, source: str = ""
) -> bool:
    """扣币。余额不足返回 False，成功返回 True。"""
    if amount <= 0:
        raise ValueError("spend amount 必须为正")
    get_currency(currency)

    def _m(user: dict[str, Any]) -> bool:
        return _apply_spend(user, currency, amount)

    return await storage.transact(user_id, _m)


async def transfer(
    from_id: str, to_id: str, currency: str, amount: int
) -> bool:
    """转账。余额不足返回 False；成功原子完成两头更新。"""
    if amount <= 0:
        raise ValueError("transfer amount 必须为正")
    get_currency(currency)

    def _m(who: dict[str, Any], whom: dict[str, Any]) -> bool:
        if not _apply_spend(who, currency, amount):
            return False
        whom["wallet"][currency]["balance"] += amount
        whom["wallet"][currency]["total_earned"] += amount
        return True

    return await storage.transact_many([from_id, to_id], _m)


async def all_wallets() -> dict[str, dict[str, Any]]:
    users = await storage.all_users()
    return {uid: user["wallet"] for uid, user in users.items()}

"""Erectheus 经济玩法数据适配层。

货币（月币/荣耀）与个人资料由 qqbot_core 统一存储；本模块只保留玩法字段
（签到连签、鱼竿、鱼饵、捕鱼记录），并把 qqbot_core 的钱包拍平成经济插件
原有代码习惯的扁平结构：

    get_user(uid) -> {
        "kleos": ..., "drachma": ...,              # 余额
        "total_kleos_earned": ..., "total_drachma_earned": ..., "total_drachma_spent": ...,
        "last_sign_in": ..., "sign_streak": ...,   # 玩法字段（sections.qqbot_economy）
        ...
    }

因此 qqbot_economy/__init__.py 不需要改动：命令层照旧调用这几个函数，
存储与代币校验都落在 qqbot_core。
"""

from typing import Any

from nonebot.log import logger
from nonebot.plugin import require

try:
    _core = require("qqbot_core")
except RuntimeError:  # pragma: no cover - 应由 bot.py 保证加载顺序
    _core = None
    logger.error(
        "[qqbot_economy] 依赖 qqbot_core 未加载：请在 bot.py 中先加载 "
        "plugins/qqbot_core，再加载本插件"
    )

SECTION = "qqbot_economy"

# 玩法字段（与货币无关）
DEFAULTS: dict[str, Any] = {
    # ---- 签到 ----
    "last_sign_in": "",
    "sign_streak": 0,
    "total_sign_days": 0,
    # ---- 捕鱼 ----
    "rod_level": 1,
    "bait": 0,
    "total_fish": 0,
    "fish_count": {},  # {rarity: n}
    "best_fish": "",
    "last_fish_ts": 0.0,
    # ---- 捕鱼活跃奖励（荣耀）每日计数 ----
    "fish_kleos_today": 0,
    "fish_kleos_date": "",
}

def _wallet_keys() -> set[str]:
    """所有代币的扁平字段（余额 + 累计），禁止通过 update_user 直接改。"""
    keys: set[str] = set()
    for key in require_core().currency.CURRENCIES:
        keys |= {key, f"total_{key}_earned", f"total_{key}_spent"}
    return keys


def require_core():
    if _core is None:
        raise RuntimeError(
            "qqbot_economy 依赖 qqbot_core，请先在 bot.py 加载 plugins/qqbot_core"
        )
    return _core


def _flatten(nested: dict[str, Any], gameplay: dict[str, Any]) -> dict[str, Any]:
    """把 qqbot_core 的嵌套用户结构拍平成经济插件习惯的扁平结构（支持任意代币）。"""
    out = dict(gameplay)
    wallet = nested["wallet"]
    for key in require_core().currency.CURRENCIES:
        w = wallet.get(key) or {"balance": 0, "total_earned": 0, "total_spent": 0}
        out[key] = int(w["balance"])
        out[f"total_{key}_earned"] = int(w["total_earned"])
        out[f"total_{key}_spent"] = int(w["total_spent"])
    return out


async def get_user(user_id: str) -> dict[str, Any]:
    """取用户数据（合并钱包与玩法字段，不存在则返回默认值，不落盘）。"""
    nested = await require_core().storage.get_user(user_id)
    gameplay = {**DEFAULTS, **nested["sections"].get(SECTION, {})}
    return _flatten(nested, gameplay)


async def update_user(user_id: str, **fields: Any) -> dict[str, Any]:
    """更新玩法字段并落盘，返回最新数据。货币字段请走 earn/spend。"""
    bad = {k for k in fields if k in _wallet_keys()}
    if bad:
        raise KeyError(f"货币字段不能直接 update：{bad}，请使用 earn/spend")
    await require_core().storage.update_section(user_id, SECTION, **fields)
    return await get_user(user_id)


async def earn(user_id: str, currency: str, amount: int, **extra: Any) -> dict[str, Any]:
    """发币并可选更新玩法字段，原子完成。"""
    if amount <= 0:
        raise ValueError("earn amount 必须为正")
    core = require_core()

    def _m(user: dict[str, Any]) -> None:
        core.wallet._apply_earn(user, currency, amount, None)
        if extra:
            user["sections"].setdefault(SECTION, {}).update(extra)

    await core.storage.transact(user_id, _m)
    return await get_user(user_id)


async def spend(
    user_id: str, currency: str, amount: int, **extra: Any
) -> dict[str, Any] | None:
    """扣币并可选更新玩法字段。余额不足返回 None。"""
    if amount <= 0:
        raise ValueError("spend amount 必须为正")
    core = require_core()

    def _m(user: dict[str, Any]) -> bool:
        ok = core.wallet._apply_spend(user, currency, amount)
        if ok and extra:
            user["sections"].setdefault(SECTION, {}).update(extra)
        return ok

    ok = await core.storage.transact(user_id, _m)
    return await get_user(user_id) if ok else None


def today_multiplier(uid: str = "") -> dict:
    """今天的荣耀倍率（周末/节日/自定义）；随机倍率日按用户单独抽。"""
    core = require_core()
    if uid:
        return core.multipliers.for_user(str(uid))
    return core.multipliers.today()


def next_event() -> dict | None:
    """下一个节日/活动及其预设倍率（由 qqbot_core 的节日日历决定）。"""
    try:
        fn = getattr(require_core().multipliers, "next_event", None)
        return fn() if fn else None
    except Exception:  # noqa: BLE001 —— 播报失败不影响签到
        return None


async def level_status(user_id: str) -> dict:
    """荣耀生效等级/星级（升阶状态由 qqbot_core 管）。"""
    return await require_core().awaken.status(user_id)


async def claim_once(section: str, key: str, date: str, by: str) -> bool:
    """原子抢占"每日首次"标记（用于每日首签）。"""
    return await require_core().storage.claim_once(section, key, date, by)


async def sign_in_reminders(user_id: str) -> list[str]:
    """签到成功后要展示的活动提醒（由 qqbot_core 判断，失败则静默）。"""
    core = require_core()
    fn = getattr(core, "sign_in_reminders", None)
    if fn is None:
        return []
    try:
        return await fn(user_id)
    except Exception:  # noqa: BLE001 —— 提醒失败不影响签到
        return []


def today_str() -> str:
    return require_core().storage.today_str()


async def all_users() -> dict[str, dict[str, Any]]:
    users = await require_core().storage.all_users()
    return {
        uid: _flatten(u, {**DEFAULTS, **u["sections"].get(SECTION, {})})
        for uid, u in users.items()
    }

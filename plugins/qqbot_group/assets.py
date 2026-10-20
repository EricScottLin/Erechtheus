"""用户资产聚合（供 /盒 使用）。

每个插件可以注册一个「资产来源」，返回若干展示行；/盒 会把所有来源的
结果拼在一起，实现「穿透所有插件」查看某个用户的家底：

- 内置：qqbot_core 钱包、qqbot_economy 玩法数据、qqbot_pigverse 猪猪宇宙
- 未来插件：
  ```python
  from nonebot import get_plugin

  assets = get_plugin("qqbot_group").module.assets
  async def my_assets(uid: str) -> list[str]:
      return [f"我的代币：{...}"]
  assets.register_asset_provider("my_plugin", my_assets)
  ```
"""

import sys
from typing import Awaitable, Callable

import nonebot
from nonebot.log import logger

AssetProvider = Callable[[str], Awaitable[list[str] | None]]

_registry: list[tuple[str, AssetProvider]] = []


def register_asset_provider(name: str, provider: AssetProvider) -> None:
    """注册一个资产来源；``provider(uid)`` 返回若干行文本或 None。"""
    _registry[:] = [(n, f) for n, f in _registry if n != name]
    _registry.append((name, provider))
    logger.info(f"[qqbot_group] 已注册资产来源：{name}")


def _module(*names: str):
    for name in names:
        module = sys.modules.get(name)
        if module is not None:
            return module
    return None


# ---------------------------------------------------------------- 内置来源
async def _core_assets(uid: str) -> list[str] | None:
    """qqbot_core 钱包余额。"""
    plugin = nonebot.get_plugin("qqbot_core")
    if plugin is None:
        return None
    module = plugin.module
    wallet = getattr(module, "wallet", None) or _module("qqbot_core.wallet")
    currency = getattr(module, "currency", None) or _module("qqbot_core.currency")
    if wallet is None or currency is None:
        return None

    balances = await wallet.balances(uid)
    lines: list[str] = []
    for key, spec in currency.CURRENCIES.items():
        amount = int(balances.get(key, 0))
        if amount or spec.consumable or spec.layer != 0:
            lines.append(f"{spec.symbol} {spec.name}：{amount:,}")
    return lines or None


async def _economy_assets(uid: str) -> list[str] | None:
    """qqbot_economy 玩法数据：签到、鱼竿、钓鱼记录。"""
    plugin = nonebot.get_plugin("qqbot_economy")
    if plugin is None:
        return None
    data = _module("qqbot_economy.data", "plugins.qqbot_economy.data")
    if data is None:
        return None

    user = await data.get_user(uid)
    lines: list[str] = []

    streak = int(user.get("sign_streak") or 0)
    total_days = int(user.get("total_sign_days") or 0)
    if streak or total_days:
        lines.append(f"📅 签到：连续 {streak} 天 · 累计 {total_days} 天")

    rod = int(user.get("rod_level") or 0)
    bait = int(user.get("bait") or 0)
    if rod or bait:
        lines.append(f"🎣 鱼竿 Lv{rod} · 鱼饵 {bait}")

    total_fish = int(user.get("total_fish") or 0)
    if total_fish:
        best = str(user.get("best_fish") or "").strip()
        lines.append(f"🐟 累计钓鱼 {total_fish} 条" + (f" · 最佳 {best}" if best else ""))

    return lines or None


async def _pigverse_assets(uid: str) -> list[str] | None:
    """qqbot_pigverse：猪猪图鉴、成就、牧场。"""
    plugin = nonebot.get_plugin("qqbot_pigverse")
    if plugin is None:
        return None
    store = getattr(plugin.module, "store", None) or _module("qqbot_pigverse.store")
    if store is None:
        return None

    lines: list[str] = []

    try:
        from nonebot_plugin_rollpig_plus.store import store as rollpig_store

        state = await rollpig_store.get_draw_state(uid)
        owned = len(state.pig_ids)
        if owned:
            lines.append(f"🐷 猪猪图鉴：{owned} 只")
    except Exception as e:  # noqa: BLE001 —— rollpig 不可用不影响其他资产
        logger.debug(f"[qqbot_group] 读取 rollpig 猪圈失败：{e}")

    try:
        record = await store.get_user(uid)
        claimed = [x for x in record.get("claimed", []) if str(x).isdigit()]
        if claimed:
            lines.append(f"🏆 已领成就：{len(claimed)} 档")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[qqbot_group] 读取 pigverse 成就失败：{e}")

    try:
        ranch = await store.get_ranch(uid)
        if ranch:
            lines.append(f"🏡 牧场：{len(ranch)} 只猪有喂食记录")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[qqbot_group] 读取 pigverse 牧场失败：{e}")

    return lines or None


register_asset_provider("qqbot_core", _core_assets)
register_asset_provider("qqbot_economy", _economy_assets)
register_asset_provider("qqbot_pigverse", _pigverse_assets)


# ---------------------------------------------------------------- 汇总
async def collect_assets(uid: str) -> list[tuple[str, list[str]]]:
    """收集所有来源的资产，返回 ``[(来源, 行列表)]``。"""
    result: list[tuple[str, list[str]]] = []
    for name, provider in _registry:
        try:
            lines = await provider(uid)
        except Exception as e:  # noqa: BLE001 —— 单个来源异常不影响整体
            logger.warning(f"[qqbot_group] 资产来源 {name} 读取失败：{e}")
            continue
        if lines:
            result.append((name, lines))
    return result

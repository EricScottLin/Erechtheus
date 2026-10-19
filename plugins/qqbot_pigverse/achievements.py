"""Phase 1：猪猪图鉴成就。

里程碑按「猪圈收集数」触发，一次性发放 Kleos（荣誉代币，不可消费）。
产能上限 = 封闭卡池，符合 CURRENCY_SPEC R3（faucet 必须有上限）与
R4（日活型 faucet 产能低于技能型）。总量 11,150 Kleos。

领取幂等：/猪猪成就 每次查询时自动补发所有已达成但未领取的里程碑。
"""

from __future__ import annotations

from typing import Any

from nonebot.log import logger

from . import store as pigstore

# (收集数阈值, Kleos 奖励, 称号)
ACHIEVEMENTS: tuple[tuple[int, int, str], ...] = (
    (10, 100, "猪圈学徒"),
    (25, 250, "拾猪人"),
    (50, 500, "猪语者"),
    (75, 800, "猪圈主"),
    (100, 1200, "百猪侯"),
    (150, 1800, "猪王"),
    (200, 2500, "万猪之神"),
    (230, 4000, "全猪图鉴"),
)

TOTAL_KLEOS = sum(a[1] for a in ACHIEVEMENTS)


async def _owned_pig_ids(uid: str) -> list[str]:
    """从 rollpig 读取猪圈（只读，不改其账本）。"""
    from nonebot_plugin_rollpig_plus.store import store as rollpig_store

    state = await rollpig_store.get_draw_state(uid)
    return list(state.pig_ids)


async def _grant_kleos(uid: str, amount: int) -> None:
    """跨插件发放 Kleos。

    不用 `from plugins.qqbot_economy.data import earn`：NoneBot 的插件管理器
    会拦截 plugins.* 的 import 并重新 exec 插件模块，导致重复注册/循环加载。
    这里直接从 sys.modules 取已加载的模块。
    """
    import sys

    mod = sys.modules.get("plugins.qqbot_economy.data") or sys.modules.get(
        "qqbot_economy.data"
    )
    if mod is None:
        raise RuntimeError("qqbot_economy 未加载，无法发放 Kleos")
    await mod.earn(uid, "kleos", amount)


async def check_and_claim(uid: str) -> tuple[list[dict[str, Any]], int]:
    """检查并补发所有已达成的里程碑。

    返回 (本次新解锁列表, 本次发放 Kleos 总数)；均无则 ([], 0)。
    """
    owned = len(await _owned_pig_ids(uid))
    rec = await pigstore.get_user(uid)
    claimed = set(int(x) for x in rec.get("claimed", []) if str(x).isdigit())

    newly: list[dict[str, Any]] = []
    total = 0
    for threshold, kleos, title in ACHIEVEMENTS:
        if owned >= threshold and threshold not in claimed:
            await _grant_kleos(uid, kleos)
            newly.append({"threshold": threshold, "kleos": kleos, "title": title})
            total += kleos
            claimed.add(threshold)

    if newly:
        titles = [t for th, _, t in ACHIEVEMENTS if th in claimed]
        await pigstore.update_user(uid, claimed=sorted(claimed), titles=titles)
        logger.info(
            f"[pigverse] 用户 {uid} 解锁 {len(newly)} 个成就，发放 Kleos {total}"
        )
    return newly, total


def progress_view(owned: int, claimed: list[int]) -> dict[str, Any]:
    """构造展示数据：当前称号、下一档、进度百分比。"""
    claimed_set = set(claimed)
    titles = [t for th, _, t in ACHIEVEMENTS if th in claimed_set]
    next_item = None
    for threshold, kleos, title in ACHIEVEMENTS:
        if owned < threshold:
            next_item = {"threshold": threshold, "kleos": kleos, "title": title}
            break
    pct = int(owned / 230 * 100)
    return {
        "owned": owned,
        "pct": pct,
        "titles": titles,
        "current_title": titles[-1] if titles else "无",
        "next": next_item,
        "claimed": sorted(claimed_set),
    }

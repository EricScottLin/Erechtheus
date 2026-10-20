"""限时活动：领金币 —— 按 QQ 群等级发放月币。

规则：
  * 活动期间每人只能领一次（防刷）
  * 发放档位按群等级映射（Config.qqbot_core_claim_tiers）
  * 受日上限约束（走 wallet._apply_earn 的 day_cap）
  * 开关由 bot 主私聊 /领金币开关 控制（settings.claim_enabled）
"""

from typing import Any

from . import storage, wallet

SECTION = "qqbot_core"


def amount_for_level(level: int, tiers: dict[int, int], default_amount: int) -> int:
    """群等级 → 发放额：取不超过该等级的最高档。"""
    best = default_amount
    for min_level, amount in sorted(tiers.items()):
        if level >= min_level:
            best = amount
    return best


def amount_for_groups(
    levels: dict[int, int],
    tiers: dict[int, int],
    default_amount: int,
    weights: list[float] | tuple[float, ...] = (1.0, 0.25, 0.15, 0.10, 0.05),
) -> tuple[int, list[tuple[int, int, float]]]:
    """多群累计发放额。

    群按成员等级从高到低排序，第 i 个群按 weights[i] 计入：
      金额 = Σ tier(等级_i) × weights[i]，四舍五入到 10。
    返回 (总额, [(等级, 该群档位额, 权重), ...])，方便播报明细。
    """
    if not levels:
        return default_amount, []
    ordered = sorted(levels.values(), reverse=True)[: len(weights)]
    rows: list[tuple[int, int, float]] = []
    total = 0.0
    for level, weight in zip(ordered, weights):
        base = amount_for_level(level, tiers, default_amount)
        rows.append((level, base, weight))
        total += base * weight
    amount = int(round(total / 10.0) * 10)
    return max(amount, 0), rows


async def claim(
    user_id: str,
    amount: int,
    meta: dict[str, Any],
    day_cap: int | None,
) -> dict[str, Any]:
    """领取一次。amount 为已算好的发放额，meta 记录来源信息。

    返回 {"status": ok|already|capped, "amount": int}。
    """

    def _m(user: dict[str, Any]) -> dict[str, Any]:
        sec = user["sections"].setdefault(SECTION, {})
        if sec.get("claim"):
            return {
                "status": "already",
                "amount": int(sec["claim"].get("amount", 0)),
            }
        granted = wallet._apply_earn(user, "drachma", amount, day_cap)
        if granted <= 0:
            return {"status": "capped", "amount": 0}
        sec["claim"] = {
            "at": storage.today_str(),
            "amount": granted,
            **meta,
        }
        return {"status": "ok", "amount": granted}

    return await storage.transact(user_id, _m)

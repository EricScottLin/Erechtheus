"""觉醒与手动升阶（maimai 旅行伙伴式）。

等级由里程决定，但**每到一个觉醒点（9/49/299/499/999/9999 级）就会卡住**，
必须手动 /升阶 才能继续升级。升阶时给一次性奖励，并获得下一颗星。

状态存在用户 sections.qqbot_core.awakened（已觉醒星数 0~6）。
"""

from typing import Any

from . import storage, wallet
from .currency import (
    AWAKENING_REWARDS,
    AWAKENINGS,
    MAX_LEVEL,
    STAR_LEVELS,
    kleos_progress,
)

SECTION = "qqbot_core"


async def awakened_of(user_id: str) -> int:
    sec = await storage.get_section(user_id, SECTION, {})
    return max(0, min(int(sec.get("awakened", 0)), len(STAR_LEVELS)))


def effective_level(raw_level: int, awakened: int) -> int:
    """当前实际生效的等级：未觉醒时卡在下一个觉醒点。"""
    if awakened >= len(STAR_LEVELS):
        return raw_level
    return min(raw_level, STAR_LEVELS[awakened])


async def status(user_id: str) -> dict[str, Any]:
    """返回当前等级状态（原始等级 / 生效等级 / 星数 / 可否升阶）。"""
    user = await storage.get_user(user_id)
    total = int(user["wallet"]["kleos"]["total_earned"])
    raw_level, _into = kleos_progress(total)
    awakened = await awakened_of(user_id)
    level = effective_level(raw_level, awakened)
    can_ascend = awakened < len(STAR_LEVELS) and raw_level >= STAR_LEVELS[awakened]
    next_threshold = STAR_LEVELS[awakened] if awakened < len(STAR_LEVELS) else None
    return {
        "total": total,
        "raw_level": raw_level,
        "level": level,
        "awakened": awakened,
        "stars": awakened,
        "can_ascend": can_ascend,
        "next_threshold": next_threshold,
        "title": AWAKENINGS[awakened - 1] if awakened else None,
        "next_title": AWAKENINGS[awakened] if awakened < len(AWAKENINGS) else None,
    }


async def ascend(user_id: str, reward_cap: int | None = None) -> dict[str, Any] | None:
    """升一阶。不可升阶返回 None；成功返回 {star, reward, ...}。"""
    st = await status(user_id)
    if not st["can_ascend"]:
        return None
    new_star = st["awakened"] + 1
    reward = AWAKENING_REWARDS.get(new_star, 0)
    if reward > 0:
        await wallet.earn(user_id, "drachma", reward, source=f"awakening star {new_star}")
    await storage.update_section(user_id, SECTION, awakened=new_star)
    return {"star": new_star, "reward": reward, "title": AWAKENINGS[new_star - 1]}

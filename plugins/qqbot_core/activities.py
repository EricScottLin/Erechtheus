"""活动提醒：签到成功后提示用户有哪些进行中的限时活动。

新增活动时只改这里：在 reminders() 里加一条检查 + 提醒文案。
活动是否进行中、用户是否已参与，都由本模块判断，玩法插件不关心细节。
"""

from . import settings, storage

SECTION = "qqbot_core"


async def _claim_claimed(user_id: str) -> bool:
    sec = await storage.get_section(user_id, SECTION, {})
    return bool(sec.get("claim"))


async def reminders(user_id: str, claim_enabled_default: bool = False) -> list[str]:
    """返回当前进行中、且该用户还没参与的活动提醒（没有则空列表）。"""
    lines: list[str] = []

    if await settings.get_claim_enabled(claim_enabled_default) and not await _claim_claimed(
        user_id
    ):
        lines.append("🎐 领金币活动进行中：群里发 /领金币 领取月币（每人限一次）")

    return lines

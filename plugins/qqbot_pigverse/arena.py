"""Phase 3：小猪斗兽场。

双方各取今日小猪，按 稀有度 → EX 等级 比较；每日 3 次。
纯娱乐，不涉及任何货币。
"""

from __future__ import annotations

from datetime import date

from . import store as pigstore
from .rarity import RARITY_ORDER, rarity_of

ARENA_DAILY_LIMIT = 3

WIN_TEXTS = (
    "你的小猪一个猪突猛进，把对方拱进了泥坑！",
    "对方的小猪被你的猪连拱三下，当场认输。",
    "你的小猪亮出獠牙，对方的小猪吓得掉头就跑。",
)
LOSE_TEXTS = (
    "你的小猪被对方按在泥里摩擦了三圈…",
    "对方小猪一记猪突，你的小猪滚出了斗兽场。",
    "你的小猪试图装死，被对方识破并补了一拱。",
)
DRAW_TEXTS = (
    "两只猪对视三秒，同时躺下开始打滚，平局。",
    "双方小猪互相拱了一下，都觉得没意思，各回各家。",
)


async def today_pig(uid: str, gid: str) -> dict | None:
    from nonebot_plugin_rollpig_plus.roll_flow import resolve_daily_pig

    res = await resolve_daily_pig(uid, gid, include_progress=True)
    if not res.pig:
        return None
    return {
        "id": str(res.pig.get("id") or ""),
        "name": str(res.pig.get("name") or "小猪"),
        "ex": int(res.ex_level or 0),
        "rarity": rarity_of(str(res.pig.get("id") or "")),
    }


async def arena_used(uid: str) -> int:
    rec = await pigstore.get_arena(uid)
    today = date.today().isoformat()
    return int(rec.get("used", 0)) if rec.get("date") == today else 0


async def consume_arena(uid: str) -> int:
    rec = await pigstore.get_arena(uid)
    today = date.today().isoformat()
    used = int(rec.get("used", 0)) if rec.get("date") == today else 0
    used += 1
    await pigstore.update_arena(uid, date=today, used=used)
    return used


def compare(mine: dict, theirs: dict) -> int:
    """1=我赢，-1=我输，0=平。稀有度优先，其次 EX。"""
    a = RARITY_ORDER.get(mine["rarity"], 0)
    b = RARITY_ORDER.get(theirs["rarity"], 0)
    if a != b:
        return 1 if a > b else -1
    if mine["ex"] != theirs["ex"]:
        return 1 if mine["ex"] > theirs["ex"] else -1
    return 0


def pick_text(result: int, rng) -> str:
    if result > 0:
        return rng.choice(WIN_TEXTS)
    if result < 0:
        return rng.choice(LOSE_TEXTS)
    return rng.choice(DRAW_TEXTS)

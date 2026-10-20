"""早安 / 晚安：记录作息，按睡眠时长发月币。

- 说「晚安」「睡了」「安安」等 → 记录入睡时间
- 说「早安」「早」「起床了」等 → 计算睡了多久，按区间发月币（每天最多一次）
- 只认「整句就是问候语」的消息，避免日常聊天误触发
"""

import asyncio
import json
import re
import sys
from datetime import datetime

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger

from . import features
from .config import plugin_config
from .helpers import format_duration

# 有效睡眠区间与月币档位（秒）
MIN_SLEEP = 3 * 3600
MAX_SLEEP = 14 * 3600
SLEEP_TIERS = (
    (3 * 3600, 5 * 3600, 15),
    (5 * 3600, 7 * 3600, 30),
    (7 * 3600, 9 * 3600, 60),
    (9 * 3600, 11 * 3600, 40),
    (11 * 3600, MAX_SLEEP + 1, 20),
)

_SUFFIX = r"(?:啦|了|呀|啊|呢|哦|喔|哟)?"
_NIGHT_RE = re.compile(
    rf"^(?:晚安|睡了|睡觉了|我睡了|去睡了|安|晚|安安){_SUFFIX}[~～!！。.，,\s]*$"
)
_MORNING_RE = re.compile(
    rf"^(?:早安|早|早上好|早啊|起床了|早早早|安|安安){_SUFFIX}[~～!！。.，,\s]*$"
)
_AMBIGUOUS = {"安", "安安"}

_lock = asyncio.Lock()


def _now() -> float:
    return datetime.now().timestamp()


def _today() -> str:
    return datetime.fromtimestamp(_now()).date().isoformat()


def state_file():
    return features.DATA_DIR / "greeting.json"


def _load() -> dict:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_group] 读取作息记录失败：{e}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(data: dict) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def classify(text: str) -> str | None:
    """返回 "night" / "morning" / None。"""
    text = (text or "").strip()
    is_night = bool(_NIGHT_RE.match(text))
    is_morning = bool(_MORNING_RE.match(text))
    if is_night and not is_morning:
        return "night"
    if is_morning and not is_night:
        return "morning"
    if is_night and is_morning:  # 安/安安 这类两可的，按时间判断
        core = re.match(r"^(?:安|安安)", text)
        hour = datetime.fromtimestamp(_now()).hour
        return "morning" if core and 5 <= hour < 18 else "night"
    return None


async def _record_night(uid: str, group_id: int) -> None:
    async with _lock:
        data = _load()
        record = data.setdefault(uid, {})
        record["night_ts"] = _now()
        record["night_group"] = group_id
        _save(data)


async def _take_night(uid: str) -> float | None:
    async with _lock:
        data = _load()
        record = data.get(uid) or {}
        ts = record.pop("night_ts", None)
        record.pop("night_group", None)
        if record:
            data[uid] = record
        else:
            data.pop(uid, None)
        _save(data)
        return float(ts) if ts else None


async def _rewarded_today(uid: str) -> bool:
    record = _load().get(uid) or {}
    return record.get("last_reward_date") == _today()


async def _mark_rewarded(uid: str) -> None:
    async with _lock:
        data = _load()
        record = data.setdefault(uid, {})
        record["last_reward_date"] = _today()
        _save(data)


def _tier_reward(seconds: int) -> int:
    for lo, hi, amount in SLEEP_TIERS:
        if lo <= seconds < hi:
            return amount
    return 0


def _core():
    return sys.modules.get("qqbot_core") or sys.modules.get("plugins.qqbot_core")


async def _reward(uid: str, amount: int) -> int:
    """给用户发月币，返回实际发放数；core 不可用时返回 0。"""
    core = _core()
    if core is None:
        return 0
    try:
        return int(
            await core.wallet.earn(uid, "drachma", amount, source="sleep_greeting")
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_group] 发放睡眠月币失败：{e}")
        return 0


async def _greeting_rule(event: GroupMessageEvent) -> bool:
    if not features.is_enabled(event.group_id, "greeting"):
        return False
    return classify(event.get_plaintext()) is not None


# 优先级 7：在关键词禁言（6）之后
greeting = on_message(rule=_greeting_rule, priority=7, block=True)


@greeting.handle()
async def handle_greeting(bot: Bot, event: GroupMessageEvent) -> None:
    kind = classify(event.get_plaintext())
    uid = event.get_user_id()

    if kind == "night":
        await _record_night(uid, event.group_id)
        await greeting.finish("🌙 晚安～已记录，明早来领月币（睡 7~9 小时最多）")

    night_ts = await _take_night(uid)
    if night_ts is None:
        await greeting.finish("☀️ 早安～")

    seconds = max(0, int(_now() - night_ts))
    if not (MIN_SLEEP <= seconds <= MAX_SLEEP):
        await greeting.finish(
            f"☀️ 早安～睡了 {format_duration(seconds)}，这次没有月币"
            f"（有效睡眠 {MIN_SLEEP // 3600}~{MAX_SLEEP // 3600} 小时）。"
        )

    if await _rewarded_today(uid):
        await greeting.finish(
            f"☀️ 早安～睡了 {format_duration(seconds)}，今天已经领过月币啦。"
        )

    amount = _tier_reward(seconds)
    if not plugin_config.qqbot_group_greeting_reward_enabled:
        await greeting.finish(f"☀️ 早安～睡了 {format_duration(seconds)}。")

    granted = await _reward(uid, amount)
    if granted > 0:
        await _mark_rewarded(uid)
        await greeting.finish(
            f"☀️ 早安～睡了 {format_duration(seconds)}，奖励 {granted} 月币 🪙"
        )
    await greeting.finish(
        f"☀️ 早安～睡了 {format_duration(seconds)}（月币系统暂时不可用）。"
    )

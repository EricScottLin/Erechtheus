"""上班 / 下班 / 打卡：工时计算与加班费。

按劳动法口径简化：
- 标准工时 8 小时/天，超出部分按 1.5 倍加班费
- 周末（周六/周日）按 2 倍
- 配置里的法定节假日按 3 倍
- 工资以月币（drachma）结算，按小时计

关键词只认整句：上班/上班了/开工、下班/下班了/收工、打卡。
"""

import asyncio
import json
import re
import sys
from datetime import datetime

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger

from . import features
from .config import plugin_config
from .helpers import format_duration

STANDARD_SECONDS = 8 * 3600
OVERTIME_RATE = 1.5
WEEKEND_RATE = 2.0
HOLIDAY_RATE = 3.0

_IN_RE = re.compile(r"^(?:上班|上班了|开工|上工)(?:啦|了|咯|喽)?[~～!！。.，,\s]*$")
_OUT_RE = re.compile(r"^(?:下班|下班了|收工|放工)(?:啦|了|咯|喽)?[~～!！。.，,\s]*$")
_PUNCH_RE = re.compile(r"^(?:打卡|打个卡)(?:啦|了|咯|喽)?[~～!！。.，,\s]*$")

_lock = asyncio.Lock()


def _now() -> float:
    return datetime.now().timestamp()


def state_file():
    return features.DATA_DIR / "work.json"


def _load() -> dict:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_group] 读取打卡记录失败：{e}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(data: dict) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _record(uid: str) -> dict:
    return _load().get(uid) or {}


async def _clock_in(uid: str, group_id: int) -> None:
    async with _lock:
        data = _load()
        data[uid] = {"in_ts": _now(), "group_id": group_id}
        _save(data)


async def _clock_out(uid: str) -> None:
    async with _lock:
        data = _load()
        data.pop(uid, None)
        _save(data)


def classify(text: str) -> str | None:
    text = (text or "").strip()
    if _IN_RE.match(text):
        return "in"
    if _OUT_RE.match(text):
        return "out"
    if _PUNCH_RE.match(text):
        return "punch"
    return None


def _is_holiday(day: datetime) -> bool:
    raw = plugin_config.qqbot_group_work_holidays or ""
    holidays = {d.strip() for d in raw.replace("，", ",").split(",") if d.strip()}
    return day.date().isoformat() in holidays


def _calc_pay(seconds: int, clock_in: float) -> tuple[int, list[str]]:
    """返回 ``(月币, 明细行)``。"""
    wage = plugin_config.qqbot_group_work_hourly_wage
    hours = seconds / 3600
    day = datetime.fromtimestamp(clock_in)

    if _is_holiday(day):
        amount = round(hours * wage * HOLIDAY_RATE)
        return amount, [f"🎉 法定节假日 {hours:g} 小时 × {wage * HOLIDAY_RATE:g} = {amount} 月币"]

    if day.weekday() >= 5:
        amount = round(hours * wage * WEEKEND_RATE)
        return amount, [f"🏖️ 周末加班 {hours:g} 小时 × {wage * WEEKEND_RATE:g} = {amount} 月币"]

    regular_s = min(seconds, STANDARD_SECONDS)
    overtime_s = max(0, seconds - STANDARD_SECONDS)
    regular_pay = round(regular_s / 3600 * wage)
    overtime_pay = round(overtime_s / 3600 * wage * OVERTIME_RATE)
    lines = []
    if regular_s:
        lines.append(
            f"正常 {format_duration(regular_s)} × {wage} = {regular_pay} 月币"
        )
    if overtime_s:
        lines.append(
            f"加班 {format_duration(overtime_s)} × {wage * OVERTIME_RATE:g} = {overtime_pay} 月币"
        )
    return regular_pay + overtime_pay, lines


def _core():
    return sys.modules.get("qqbot_core") or sys.modules.get("plugins.qqbot_core")


async def _reward(uid: str, amount: int) -> int:
    core = _core()
    if core is None:
        return 0
    try:
        return int(await core.wallet.earn(uid, "drachma", amount, source="work"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_group] 发放工资失败：{e}")
        return 0


async def _work_rule(event: GroupMessageEvent) -> bool:
    if not features.is_enabled(event.group_id, "work"):
        return False
    return classify(event.get_plaintext()) is not None


work = on_message(rule=_work_rule, priority=7, block=True)


@work.handle()
async def handle_work_event(event: GroupMessageEvent) -> None:
    kind = classify(event.get_plaintext())
    uid = event.get_user_id()
    record = _record(uid)
    working = bool(record.get("in_ts"))

    if kind == "punch":
        kind = "out" if working else "in"

    if kind == "in":
        if working:
            since = datetime.fromtimestamp(float(record["in_ts"])).strftime("%H:%M")
            await work.finish(f"💼 你 {since} 已经打过上班卡了，先「下班」再来～")
        await _clock_in(uid, event.group_id)
        now = datetime.fromtimestamp(_now()).strftime("%H:%M")
        await work.finish(f"💼 上班打卡成功（{now}），下班回来说「下班」～")

    # 下班
    if not working:
        await work.finish("你还没上班打卡哦，先说「上班」～")

    clock_in = float(record["in_ts"])
    await _clock_out(uid)
    seconds = max(0, int(_now() - clock_in))
    min_seconds = plugin_config.qqbot_group_work_min_minutes * 60

    if seconds < min_seconds:
        await work.finish(
            f"这次只工作了 {format_duration(seconds)}，不到 "
            f"{plugin_config.qqbot_group_work_min_minutes} 分钟，不算工资～"
        )

    max_seconds = plugin_config.qqbot_group_work_max_hours * 3600
    capped = seconds > max_seconds
    seconds = min(seconds, max_seconds)

    amount, detail = _calc_pay(seconds, clock_in)
    end = datetime.fromtimestamp(_now()).strftime("%H:%M")
    start = datetime.fromtimestamp(clock_in).strftime("%H:%M")
    lines = [
        f"💼 下班打卡：{start} → {end}，共 {format_duration(seconds)}"
        + ("（超过上限已截断）" if capped else ""),
        *detail,
        f"合计 {amount} 月币 🪙",
    ]

    if amount <= 0:
        await work.finish("\n".join(lines))

    granted = await _reward(uid, amount)
    if granted > 0:
        await work.finish("\n".join(lines))
    lines[-1] = "月币系统暂时不可用，工资没发出去（打卡已记录）"
    await work.finish("\n".join(lines))

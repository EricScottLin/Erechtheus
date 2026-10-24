"""荣耀倍率：周末 / 节日 / 自定义日期的加成。

规则存在 qqbot_core 数据目录的 multipliers.json，bot 主可用 /倍率 命令修改：
  /倍率                       查看今天倍率与近期安排
  /倍率 日历 [天数]            列出节日日历（国庆/除夕 20 倍、传统节日 10 倍、西方节日 5 倍）
  /倍率 周末 3                 周末统一 ×3
  /倍率 2026-11-11 5 生日       指定日期 ×5
  /倍率 区间 2026-10-01 2026-10-07 5 国庆   日期区间 ×5
  /倍率 删除 2026-11-11        删除某条日期规则

规则字段：
  mult   倍率（1.0 = 不加成）
  uncap  是否同时取消捕鱼荣耀日上限（大红日子用）
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from nonebot.log import logger

from . import festivals, storage

RULES_FILE: Path = storage.DATA_DIR / "multipliers.json"

DEFAULTS: dict[str, Any] = {
    "weekend": 2.0,
    "dates": {},  # "2026-11-11": {"mult": 5.0, "name": "生日", "uncap": False}
    "ranges": [],  # 自定义区间，节日日历里的假期不在这里
}


def _load() -> dict[str, Any]:
    if RULES_FILE.exists():
        try:
            raw = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                merged = json.loads(json.dumps(DEFAULTS))
                merged.update(raw)
                return merged
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_core] 读取倍率规则失败：{e}")
    return json.loads(json.dumps(DEFAULTS))


def _save(rules: dict[str, Any]) -> None:
    storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RULES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RULES_FILE)


def for_date(d: date) -> dict[str, Any]:
    """返回某天的倍率信息 {"mult", "name", "uncap"}。"""
    rules = _load()
    key = d.isoformat()
    if key in rules.get("dates", {}):
        item = rules["dates"][key]
        return {
            "mult": float(item.get("mult", 1.0)),
            "name": item.get("name", key),
            "uncap": bool(item.get("uncap", False)),
            "tier": festivals.tier_name(float(item.get("mult", 1.0))),
        }
    for r in rules.get("ranges", []):
        try:
            start = date.fromisoformat(r["start"])
            end = date.fromisoformat(r["end"])
        except Exception:  # noqa: BLE001
            continue
        if start <= d <= end:
            mult = float(r.get("mult", 1.0))
            return {
                "mult": mult,
                "name": r.get("name", "节日"),
                "uncap": bool(r.get("uncap", False)),
                "tier": festivals.tier_name(mult),
            }
    hit = festivals.lookup(d)  # 内置节日日历
    if hit:
        hit.setdefault("uncap", False)
        return hit
    if d.weekday() >= 5:  # 周六日
        mult = float(rules.get("weekend", 1.0))
        return {"mult": mult, "name": "周末", "uncap": False, "tier": festivals.tier_name(mult)}
    return {"mult": 1.0, "name": "", "uncap": False, "tier": ""}


def today() -> dict[str, Any]:
    return for_date(date.today())


def for_user(uid: str, d: date | None = None) -> dict[str, Any]:
    """当天倍率，随机倍率日按 uid 单独抽签（同一个人当天固定）。"""
    d = d or date.today()
    info = for_date(d)
    spec = info.get("dice")
    if not spec:
        return info
    n = festivals.roll_n(d.isoformat(), spec["weights"], salt=str(uid))
    mult = float(spec["fixed"] * 2 ** n)          # 荣耀跟着骰子放大
    bonus = int(spec["unit"] * 2 ** n)            # 月币奖励同一倍数
    info.update(
        mult=mult,
        tier=festivals.tier_name(mult),
        note=festivals.DICE_NOTES.get(n, ""),
        dice={**spec, "n": n, "value": 2 ** n, "bonus": bonus},
        drachma_bonus=bonus,
    )
    return info


def mult_text(info: dict[str, Any]) -> str:
    """倍率的展示文案：随机倍率日标「+🎲」；没抽签时显示区间。"""
    d = info.get("dice")
    if not d:
        return f"×{float(info.get('mult', 1)):g}"
    if d.get("n"):
        return f"×{float(info['mult']):g}+🎲"
    lo = d["fixed"] * 2 ** min(d["weights"])
    hi = d["fixed"] * 2 ** max(d["weights"])
    return f"×{lo:g}~×{hi:g}+🎲"


def upcoming(days: int = 60, limit: int = 8) -> list[dict[str, Any]]:
    """未来若干天里倍率 > 1 的日子，连续同名同倍率的合并成一段。"""
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for i in range(days):
        d = date.today() + timedelta(days=i)
        info = for_date(d)
        if info["mult"] <= 1 or info["name"] == "周末":
            cur = None
            continue
        if cur and cur["name"] == info["name"] and cur["mult"] == info["mult"]:
            cur["end"] = d.isoformat()
            continue
        cur = {**info, "start": d.isoformat(), "end": d.isoformat()}
        items.append(cur)
        if len(items) >= limit:
            break
    return items


def next_event(days: int = 400) -> dict[str, Any] | None:
    """下一个"有加成"的日子（跳过纯周末），给签到播报用。

    返回 {date, days_left, mult, name, tier, uncap}；没有则 None。
    """
    start = date.today()
    for i in range(1, days + 1):
        d = start + timedelta(days=i)
        info = for_date(d)
        if info["mult"] > 1 and info["name"] != "周末":
            return {**info, "date": d.isoformat(), "days_left": i}
    return None


def festival_listing(days: int = 60) -> list[tuple[str, dict[str, Any]]]:
    """节日日历里未来若干天的节日（不含周末）。"""
    return festivals.listing(date.today(), days)


def set_weekend(mult: float) -> None:
    rules = _load()
    rules["weekend"] = float(mult)
    _save(rules)


def set_date(day: str, mult: float, name: str = "", uncap: bool = False) -> None:
    rules = _load()
    rules.setdefault("dates", {})[day] = {
        "mult": float(mult),
        "name": name or day,
        "uncap": uncap,
    }
    _save(rules)


def remove_date(day: str) -> bool:
    rules = _load()
    existed = rules.get("dates", {}).pop(day, None) is not None
    _save(rules)
    return existed


def set_range(start: str, end: str, mult: float, name: str = "", uncap: bool = False) -> None:
    rules = _load()
    rules.setdefault("ranges", []).append(
        {"start": start, "end": end, "mult": float(mult), "name": name or "节日", "uncap": uncap}
    )
    _save(rules)

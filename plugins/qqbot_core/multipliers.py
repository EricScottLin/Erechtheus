"""荣耀倍率：周末 / 节日 / 自定义日期的加成。

规则存在 qqbot_core 数据目录的 multipliers.json，bot 主可用 /倍率 命令修改：
  /倍率                       查看今天倍率与近期安排
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

from . import storage

RULES_FILE: Path = storage.DATA_DIR / "multipliers.json"

DEFAULTS: dict[str, Any] = {
    "weekend": 2.0,
    "dates": {},  # "2026-11-11": {"mult": 5.0, "name": "生日", "uncap": False}
    "ranges": [  # 节日区间
        {"start": "2026-10-01", "end": "2026-10-07", "mult": 5.0, "name": "国庆"}
    ],
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
        }
    for r in rules.get("ranges", []):
        try:
            start = date.fromisoformat(r["start"])
            end = date.fromisoformat(r["end"])
        except Exception:  # noqa: BLE001
            continue
        if start <= d <= end:
            return {
                "mult": float(r.get("mult", 1.0)),
                "name": r.get("name", "节日"),
                "uncap": bool(r.get("uncap", False)),
            }
    if d.weekday() >= 5:  # 周六日
        return {"mult": float(rules.get("weekend", 1.0)), "name": "周末", "uncap": False}
    return {"mult": 1.0, "name": "", "uncap": False}


def today() -> dict[str, Any]:
    return for_date(date.today())


def upcoming(days: int = 60, limit: int = 8) -> list[tuple[str, dict[str, Any]]]:
    """未来若干天里倍率 > 1 的日期（合并连续区间，只列开始日）。"""
    rules = _load()
    items: list[tuple[str, dict[str, Any]]] = []
    for i in range(days):
        d = date.today() + timedelta(days=i)
        info = for_date(d)
        if info["mult"] > 1 and (not items or items[-1][1]["name"] != info["name"]):
            items.append((d.isoformat(), info))
        if len(items) >= limit:
            break
    return items


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

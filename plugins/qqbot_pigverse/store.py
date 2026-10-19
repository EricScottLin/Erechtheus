"""pigverse 状态存储。

只存 pigverse 自己的状态（成就领取、斗兽场次数、牧场喂食），
**不写 rollpig 的内部账本**，避免与其数据结构/并发控制冲突。

文件：<localstore 插件数据目录>/pigverse.json
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nonebot.log import logger

try:
    from nonebot_plugin_localstore import get_plugin_data_file

    DATA_FILE: Path = get_plugin_data_file("pigverse.json")
except Exception:  # pragma: no cover - localstore 缺失时兜底
    DATA_FILE = Path.cwd() / "data" / "qqbot_pigverse" / "pigverse.json"

_lock = asyncio.Lock()
_cache: dict[str, Any] | None = None


def _default_data() -> dict[str, Any]:
    return {
        "achievements": {},  # {uid: {"claimed": [10, 25, ...], "titles": [...]}}
        "arena": {},         # {uid: {"date": "...", "used": 0}}
        "ranch": {},         # {uid: {pig_id: {"fed": n, "last_fed": "..."}}}
    }


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if DATA_FILE.exists():
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in _default_data().items():
                    raw.setdefault(k, v)
                _cache = raw
                return _cache
            logger.warning(f"[pigverse] {DATA_FILE} 结构异常，已重置")
        except Exception as e:
            logger.warning(f"[pigverse] 读取 {DATA_FILE} 失败：{e}，已重置")
    _cache = _default_data()
    return _cache


async def _save() -> None:
    if _cache is None:
        return
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


async def get_user(uid: str) -> dict[str, Any]:
    async with _lock:
        data = _load()
        user = data["achievements"].setdefault(uid, {"claimed": [], "titles": []})
        return dict(user)


async def update_user(uid: str, **fields: Any) -> dict[str, Any]:
    async with _lock:
        data = _load()
        user = data["achievements"].setdefault(uid, {"claimed": [], "titles": []})
        user.update(fields)
        await _save()
        return dict(user)


async def get_arena(uid: str) -> dict[str, Any]:
    async with _lock:
        data = _load()
        return dict(data["arena"].get(uid, {}))


async def update_arena(uid: str, **fields: Any) -> dict[str, Any]:
    async with _lock:
        data = _load()
        rec = data["arena"].setdefault(uid, {})
        rec.update(fields)
        await _save()
        return dict(rec)


async def get_ranch(uid: str) -> dict[str, Any]:
    async with _lock:
        data = _load()
        return dict(data["ranch"].get(uid, {}))


async def update_ranch(uid: str, pig_id: str, **fields: Any) -> dict[str, Any]:
    async with _lock:
        data = _load()
        pigs = data["ranch"].setdefault(uid, {})
        rec = pigs.setdefault(pig_id, {"fed": 0, "last_fed": ""})
        rec.update(fields)
        await _save()
        return dict(rec)

"""用户数据持久化层。

用单个 JSON 文件存储所有用户的经济数据，配合 asyncio.Lock 保证并发安全。
数据目录由 nonebot_plugin_localstore 提供（容器内 /app/data/qqbot_economy，
宿主机通过 docker-compose 挂载 ./data 持久化）。

为什么不引入数据库：
  签到/捕鱼是低频写操作，单文件 JSON 足够，且零外部依赖，
  备份就是复制一个文件，换机器迁移最省事。

货币字段命名与 currency.py 的 CURRENCIES key 一致：
  kleos    荣誉代币（不可消费）
  drachma  软通货（可消费）
另存 total_*_earned / total_*_spent 累计字段，供规范 §6 的 M_d / S_d / ρ 指标计算。
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from nonebot.log import logger

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover - localstore 缺失时退到当前目录
    DATA_DIR = Path.cwd() / "data" / "qqbot_economy"

DATA_FILE = DATA_DIR / "users.json"

_lock = asyncio.Lock()
_cache: dict[str, dict[str, Any]] | None = None


def _default_user() -> dict[str, Any]:
    return {
        # ---- 代币（key 与 currency.py 对应）----
        "kleos": 0,
        "drachma": 0,
        # ---- 累计字段（监控指标用，规范 §6）----
        "total_kleos_earned": 0,
        "total_drachma_earned": 0,
        "total_drachma_spent": 0,
        # ---- 签到 ----
        "last_sign_in": "",
        "sign_streak": 0,
        "total_sign_days": 0,
        # ---- 捕鱼 ----
        "rod_level": 1,
        "bait": 0,
        "total_fish": 0,
        "fish_count": {},  # {rarity: n}
        "best_fish": "",
        "last_fish_ts": 0.0,
    }


def _load() -> dict[str, dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    if DATA_FILE.exists():
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _cache = raw
                return _cache
            logger.warning(f"[qqbot_economy] {DATA_FILE} 结构异常，已重置")
        except Exception as e:
            logger.warning(f"[qqbot_economy] 读取 {DATA_FILE} 失败：{e}，已重置")
    _cache = {}
    return _cache


async def _save() -> None:
    if _cache is None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)  # 原子替换，避免写一半损坏


def _ensure_fields(user: dict[str, Any]) -> None:
    """兜底补齐新版本新增字段，旧数据文件无需迁移。"""
    for k, v in _default_user().items():
        user.setdefault(k, v)


async def get_user(user_id: str) -> dict[str, Any]:
    """取用户数据（不存在则创建默认值，但不落盘）。"""
    async with _lock:
        data = _load()
        user = data.get(user_id)
        if user is None:
            user = _default_user()
            data[user_id] = user
        _ensure_fields(user)
        return dict(user)


async def update_user(user_id: str, **fields: Any) -> dict[str, Any]:
    """更新用户字段并立即落盘，返回最新数据。"""
    async with _lock:
        data = _load()
        user = data.get(user_id)
        if user is None:
            user = _default_user()
            data[user_id] = user
        _ensure_fields(user)
        user.update(fields)
        await _save()
        return dict(user)


async def earn(user_id: str, currency: str, amount: int, **extra: Any) -> dict[str, Any]:
    """发放代币并累加对应 total_*_earned（监控用）。"""
    if amount <= 0:
        raise ValueError("earn amount 必须为正")
    async with _lock:
        data = _load()
        user = data.get(user_id)
        if user is None:
            user = _default_user()
            data[user_id] = user
        _ensure_fields(user)
        user[currency] = user.get(currency, 0) + amount
        user[f"total_{currency}_earned"] = (
            user.get(f"total_{currency}_earned", 0) + amount
        )
        user.update(extra)
        await _save()
        return dict(user)


async def spend(user_id: str, currency: str, amount: int, **extra: Any) -> dict[str, Any] | None:
    """扣除代币。余额不足返回 None，成功返回最新数据。"""
    if amount <= 0:
        raise ValueError("spend amount 必须为正")
    async with _lock:
        data = _load()
        user = data.get(user_id)
        if user is None:
            user = _default_user()
            data[user_id] = user
        _ensure_fields(user)
        if user.get(currency, 0) < amount:
            return None
        user[currency] -= amount
        user[f"total_{currency}_spent"] = (
            user.get(f"total_{currency}_spent", 0) + amount
        )
        user.update(extra)
        await _save()
        return dict(user)


def today_str() -> str:
    return date.today().isoformat()


async def all_users() -> dict[str, dict[str, Any]]:
    async with _lock:
        return dict(_load())

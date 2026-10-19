"""统一用户数据存储。

结构与职责：
  profile   个人信息：昵称、签名、入坑日期
  wallet    钱包：{代币key: {balance, total_earned, total_spent, daily_earned}}
  sections  其他插件的自留地，按插件名命名空间隔离（如 qqbot_economy 的鱼竿/鱼饵）

货币与个人信息都只在这里落盘，其他插件通过 qqbot_core 暴露的接口读写，
不允许碰这个 JSON 的内部结构。

存储用单文件 JSON + asyncio.Lock：写操作低频，备份就是复制一个文件。
数据目录由 nonebot_plugin_localstore 提供（容器内 /app/data/qqbot_core，
宿主机通过 docker-compose 挂载 ./data 持久化）。
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from nonebot.log import logger

from .currency import CURRENCIES

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover - localstore 缺失时退到当前目录
    DATA_DIR = Path.cwd() / "data" / "qqbot_core"

USERS_FILE = DATA_DIR / "users.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

_lock = asyncio.Lock()
_cache: dict[str, dict[str, Any]] | None = None
_settings_cache: dict[str, Any] | None = None


def today_str() -> str:
    return date.today().isoformat()


def _default_user() -> dict[str, Any]:
    return {
        "profile": {
            "nickname": "",
            "signature": "",
            "joined_at": today_str(),
        },
        "wallet": {},
        "sections": {},
    }


def _default_wallet() -> dict[str, Any]:
    return {"balance": 0, "total_earned": 0, "total_spent": 0, "daily_earned": {}}


def _ensure(user: dict[str, Any]) -> None:
    """兜底补齐新版本新增字段，旧数据文件无需迁移。"""
    profile = user.setdefault("profile", {})
    for k, v in _default_user()["profile"].items():
        profile.setdefault(k, v)
    wallet = user.setdefault("wallet", {})
    for key in CURRENCIES:
        w = wallet.setdefault(key, _default_wallet())
        for k, v in _default_wallet().items():
            w.setdefault(k, v)
        w.setdefault("daily_earned", {})
    user.setdefault("sections", {})


def _load() -> dict[str, dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    if USERS_FILE.exists():
        try:
            raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _cache = raw
                return _cache
            logger.warning(f"[qqbot_core] {USERS_FILE} 结构异常，已重置")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_core] 读取 {USERS_FILE} 失败：{e}，已重置")
    _cache = {}
    return _cache


def _save() -> None:
    if _cache is None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)  # 原子替换，避免写一半损坏


def _ensure_locked(uid: str) -> dict[str, Any]:
    data = _load()
    user = data.get(uid)
    if user is None:
        user = _default_user()
        data[uid] = user
    _ensure(user)
    return user


async def transact(uid: str, mutate: Callable[[dict[str, Any]], Any]) -> Any:
    """在锁内对单个用户执行读改写，成功后落盘。mutate 抛异常则不保存。"""
    async with _lock:
        user = _ensure_locked(uid)
        result = mutate(user)
        _save()
        return result


async def transact_many(
    uids: list[str], mutate: Callable[..., Any]
) -> Any:
    """在锁内对多个用户执行读改写（转账用），成功后落盘。"""
    async with _lock:
        users = [_ensure_locked(uid) for uid in uids]
        result = mutate(*users)
        _save()
        return result


async def get_user(uid: str) -> dict[str, Any]:
    """取用户数据（不存在则创建默认值，但不落盘）。返回副本。"""
    async with _lock:
        user = _ensure_locked(uid)
        return json.loads(json.dumps(user, ensure_ascii=False))


async def get_profile(uid: str) -> dict[str, Any]:
    return (await get_user(uid))["profile"]


async def update_profile(uid: str, **fields: Any) -> dict[str, Any]:
    """更新个人信息字段（只接受 profile 的合法字段）。"""
    allowed = {"nickname", "signature"}
    for k in fields:
        if k not in allowed:
            raise KeyError(f"profile 不允许的字段：{k}")

    def _m(user: dict[str, Any]) -> dict[str, Any]:
        user["profile"].update(fields)
        return dict(user["profile"])

    return await transact(uid, _m)


async def get_section(
    uid: str, section: str, defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    """取某插件命名空间下的用户数据（不存在则用 defaults 兜底，不落盘）。"""
    user = await get_user(uid)
    data = user["sections"].get(section)
    if data is None:
        data = dict(defaults or {})
    else:
        data = dict(data)
        if defaults:
            for k, v in defaults.items():
                data.setdefault(k, v)
    return data


async def update_section(
    uid: str, section: str, **fields: Any
) -> dict[str, Any]:
    """更新某插件命名空间下的用户数据。"""

    def _m(user: dict[str, Any]) -> dict[str, Any]:
        sec = user["sections"].setdefault(section, {})
        sec.update(fields)
        return dict(sec)

    return await transact(uid, _m)


async def all_users() -> dict[str, dict[str, Any]]:
    async with _lock:
        return json.loads(json.dumps(_load(), ensure_ascii=False))


# ---------------------------------------------------------------- 全局运行时设置
def _load_settings() -> dict[str, Any]:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _settings_cache = raw
                return _settings_cache
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_core] 读取 {SETTINGS_FILE} 失败：{e}")
    _settings_cache = {}
    return _settings_cache


def _save_settings() -> None:
    if _settings_cache is None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(_settings_cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(SETTINGS_FILE)


async def read_settings() -> dict[str, Any]:
    async with _lock:
        return dict(_load_settings())


async def write_setting(key: str, value: Any) -> dict[str, Any]:
    async with _lock:
        settings = _load_settings()
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
        _save_settings()
        return dict(settings)

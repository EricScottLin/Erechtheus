"""V-Archive ID 绑定存储。

JSON 落在 localstore 的插件数据目录；localstore 不可用时退回进程目录，
避免因为少一个可选依赖导致整个插件不可用。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.Lock()


def _data_file() -> Path:
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        base = get_plugin_data_dir()
    except Exception:  # pragma: no cover - localstore 缺失时退到 cwd
        base = Path.cwd() / "data" / "qqbot_djmax"
    base.mkdir(parents=True, exist_ok=True)
    return base / "bind_data.json"


def _load() -> dict[str, str]:
    path = _data_file()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _save(data: dict[str, str]) -> None:
    path = _data_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_bound(user_id: str) -> str | None:
    with _LOCK:
        return _load().get(str(user_id))


def bind(user_id: str, va_id: str) -> None:
    with _LOCK:
        data = _load()
        data[str(user_id)] = va_id
        _save(data)


def unbind(user_id: str) -> bool:
    with _LOCK:
        data = _load()
        if str(user_id) not in data:
            return False
        del data[str(user_id)]
        _save(data)
        return True

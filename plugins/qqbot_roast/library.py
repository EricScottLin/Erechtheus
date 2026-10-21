"""梗图库：触发词 → 表情包，按群隔离。

目录结构（数据目录由 localstore 提供，容器内 /app/data/qqbot_roast）：

    memes.json        {group_id: {trigger: [file, file, ...]}}
    memes/<group_id>/<sha1>.<ext>   图片本体

同一张图重复上传会按内容哈希去重。
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from nonebot.log import logger

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover
    DATA_DIR = Path.cwd() / "data" / "qqbot_roast"

INDEX_FILE = DATA_DIR / "memes.json"
_lock = asyncio.Lock()

# 允许的图片扩展名（按文件头嗅探）
_EXT_MAP = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}


def _sniff_ext(data: bytes) -> str:
    for magic, ext in _EXT_MAP.items():
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _load() -> dict[str, Any]:
    if not INDEX_FILE.is_file():
        return {}
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_roast] 读取梗图索引失败：{e}")
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for gid, triggers in raw.items():
        if not isinstance(triggers, dict):
            continue
        out[str(gid)] = {
            str(k): [str(f) for f in v] for k, v in triggers.items() if isinstance(v, list)
        }
    return out


def _save(data: dict[str, Any]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX_FILE)


def image_path(group_id: int, name: str) -> Path:
    return DATA_DIR / "memes" / str(group_id) / name


async def add_meme(group_id: int, trigger: str, image: bytes) -> tuple[str, bool]:
    """保存一张图，返回 ``(文件名, 是否新增)``。"""
    trigger = trigger.strip()
    digest = hashlib.sha1(image).hexdigest()
    ext = _sniff_ext(image)
    name = f"{digest[:16]}{ext}"

    async with _lock:
        data = _load()
        triggers = data.setdefault(str(group_id), {})
        files = triggers.setdefault(trigger, [])
        path = image_path(group_id, name)
        is_new = name not in files
        if is_new:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
            files.append(name)
        _save(data)
    return name, is_new


async def remove_meme(group_id: int, trigger: str, name: str | None = None) -> int:
    """删除某个触发词的图（name 为空则全删），返回删除数量。"""
    async with _lock:
        data = _load()
        triggers = data.get(str(group_id)) or {}
        files = triggers.get(trigger.strip())
        if not files:
            return 0
        if name:
            targets = [f for f in files if f == name]
        else:
            targets = list(files)
        if not targets:
            return 0
        for target in targets:
            try:
                image_path(group_id, target).unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_roast] 删除梗图 {target} 失败：{e}")
            if target in files:
                files.remove(target)
        if not files:
            triggers.pop(trigger.strip(), None)
        if triggers:
            data[str(group_id)] = triggers
        else:
            data.pop(str(group_id), None)
        _save(data)
        return len(targets)


def triggers_of(group_id: int) -> dict[str, list[str]]:
    return dict(_load().get(str(group_id)) or {})


def all_triggers(group_id: int) -> list[str]:
    return sorted(triggers_of(group_id).keys(), key=len, reverse=True)


def memes_for(group_id: int, trigger: str) -> list[str]:
    return list((triggers_of(group_id).get(trigger.strip()) or []))

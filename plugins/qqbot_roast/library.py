"""梗图库：触发词 → 表情包（全局共享，按群开关控制是否生效）。

目录结构（数据目录由 localstore 提供，容器内 /app/data/qqbot_roast）：

    memes.json                       {"trigger": [file, file, ...]}
    memes/<sha1>.<ext>               图片本体

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
MEMES_DIR = DATA_DIR / "memes"
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


def _load() -> dict[str, list[str]]:
    """读取索引：``{trigger: [file, ...]}``。

    兼容旧结构 ``{group_id: {trigger: [...]}}``（按群隔离），读到后自动合并。
    """
    if not INDEX_FILE.is_file():
        return {}
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_roast] 读取梗图索引失败：{e}")
        return {}
    if not isinstance(raw, dict):
        return {}

    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):  # 新结构
            files = [str(f) for f in value]
            if files:
                out.setdefault(str(key), []).extend(files)
        elif isinstance(value, dict):  # 旧结构：按群
            for trigger, files in value.items():
                if not isinstance(files, list):
                    continue
                out.setdefault(str(trigger), []).extend(str(f) for f in files)
    # 去重保序
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


def _save(data: dict[str, list[str]]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX_FILE)


def image_path(name: str) -> Path:
    return MEMES_DIR / name


async def add_meme(trigger: str, image: bytes) -> tuple[str, bool]:
    """保存一张图，返回 ``(文件名, 是否新增)``。"""
    trigger = trigger.strip()
    digest = hashlib.sha1(image).hexdigest()
    name = f"{digest[:16]}{_sniff_ext(image)}"

    async with _lock:
        data = _load()
        files = data.setdefault(trigger, [])
        is_new = name not in files
        if is_new:
            MEMES_DIR.mkdir(parents=True, exist_ok=True)
            image_path(name).write_bytes(image)
            files.append(name)
        _save(data)
    return name, is_new


async def remove_meme(trigger: str) -> int:
    """删除某个触发词的所有图，返回删除数量。"""
    async with _lock:
        data = _load()
        files = data.pop(trigger.strip(), None)
        if not files:
            return 0
        for target in files:
            try:
                image_path(target).unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_roast] 删除梗图 {target} 失败：{e}")
        _save(data)
        return len(files)


def triggers_of() -> dict[str, list[str]]:
    return dict(_load())


def all_triggers() -> list[str]:
    return sorted(_load().keys(), key=len, reverse=True)


def memes_for(trigger: str) -> list[str]:
    return list(_load().get(trigger.strip()) or [])

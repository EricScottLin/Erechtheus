"""小猪表情包库（PigPig）。

- 预置：从 PigHub 下载的猪猪表情包（source=pighub）
- 投稿：群友 `/猪猪表情 上传` 回复图片即可入库（source=user）
- 索引与数据文件都放在 pigverse 的 localstore 数据目录（容器 /app/data/qqbot_pigverse）
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from nonebot.log import logger

from .store import DATA_FILE

MEMES_DIR = DATA_FILE.parent / "memes"
INDEX_FILE = DATA_FILE.parent / "memes.json"

_lock = asyncio.Lock()
_cache: list[dict[str, Any]] | None = None

# 用户投稿大小上限：2MB（QQ 表情包够用，防止塞大图）
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
# 随机发送时的上限：超过则不参与随机（base64 发送会膨胀，避免大图超时）
MAX_SEND_BYTES = 2_500_000


def sniff_ext(data: bytes) -> str | None:
    """按文件头判断真实格式（PigHub 的 .jpg 经常实际是 WebP）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _load() -> list[dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    if INDEX_FILE.exists():
        try:
            raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                _cache = raw
                return _cache
            logger.warning(f"[pigverse] {INDEX_FILE} 结构异常，已重置")
        except Exception as e:
            logger.warning(f"[pigverse] 读取表情包索引失败：{e}，已重置")
    _cache = []
    return _cache


async def _save() -> None:
    if _cache is None:
        return
    MEMES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(INDEX_FILE)


async def count() -> int:
    async with _lock:
        return len(_load())


async def random_meme() -> Path | None:
    import random

    async with _lock:
        items = [
            m
            for m in _load()
            if (MEMES_DIR / m["file"]).exists()
            and (MEMES_DIR / m["file"]).stat().st_size <= MAX_SEND_BYTES
        ]
        if not items:
            return None
        return MEMES_DIR / random.choice(items)["file"]


async def add_meme(
    data: bytes,
    *,
    source: str = "user",
    title: str = "",
    uploader: str = "",
) -> dict[str, Any] | None:
    """保存一张表情包并写入索引；格式不识别或超限返回 None。"""
    if len(data) > MAX_UPLOAD_BYTES:
        return None
    ext = sniff_ext(data)
    if ext is None:
        return None

    async with _lock:
        items = _load()
        next_id = max((int(m.get("id", 0)) for m in items), default=0) + 1
        fname = f"{next_id:04d}{ext}"
        MEMES_DIR.mkdir(parents=True, exist_ok=True)
        (MEMES_DIR / fname).write_bytes(data)
        item = {
            "id": next_id,
            "file": fname,
            "source": source,
            "title": title[:60],
            "uploader": uploader,
            "ts": int(time.time()),
        }
        items.append(item)
        await _save()
        logger.info(f"[pigverse] 新增表情包 #{next_id} source={source}")
        return dict(item)


async def delete_meme(meme_id: int) -> bool:
    async with _lock:
        items = _load()
        for idx, m in enumerate(items):
            if int(m.get("id", -1)) == meme_id:
                path = MEMES_DIR / m["file"]
                if path.exists():
                    path.unlink(missing_ok=True)
                items.pop(idx)
                await _save()
                return True
        return False


async def list_memes(limit: int = 8) -> list[dict[str, Any]]:
    async with _lock:
        return [dict(m) for m in _load()][:limit]

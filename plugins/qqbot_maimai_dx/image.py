"""舞萌直连的图片处理：把 QQ 消息里的二维码图片取成 bytes。

流程：``image`` 段 → ``file://`` 绝对路径 → 协议端缓存目录（/llbot-data/temp）
→ ``url`` 下载。只取第一张，超过上限直接放弃；任何失败都返回 None，由调用方提示。

为什么不用 qqbot_ai 的 vision 模块：那是给大模型看图的（base64 data URL），
这里只要原始字节做二维码解码，单独一份更小的实现，避免跨插件耦合。
"""

import re
from pathlib import Path
from typing import Optional

import httpx
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.log import logger

_FILE_URI = re.compile(r"^file://(.+)$")
# 协议端（llbot）容器里的图片缓存目录，通过 docker-compose 只读挂进 NoneBot 容器。
_CACHE_DIRS = (Path("/llbot-data/temp"), Path("/llbot-data"))


def _image_segments(event: MessageEvent) -> list[tuple[str, str]]:
    """返回 [(file, url), ...]，只保留 image 段。"""
    items = []
    try:
        message = event.get_message()
    except Exception:  # pragma: no cover - 事件结构异常时不影响命令本身
        return items
    for segment in message:
        if getattr(segment, "type", None) != "image":
            continue
        data = getattr(segment, "data", {}) or {}
        file = str(data.get("file") or "")
        url = str(data.get("url") or "")
        if file or url:
            items.append((file, url))
    return items


def find_in_cache(file_name: str) -> Optional[Path]:
    """按文件名在协议端缓存目录里找图（只允许缓存目录下的普通文件）。"""
    name = Path(file_name).name
    if not name or name in (".", ".."):
        return None
    for base in _CACHE_DIRS:
        if not base.is_dir():
            continue
        candidate = base / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _read_local(file: str) -> Optional[bytes]:
    match = _FILE_URI.match(file)
    if match:
        path = Path(match.group(1))
        if path.is_file():
            return path.read_bytes()
    path = Path(file)
    if path.is_absolute() and path.is_file():
        return path.read_bytes()
    found = find_in_cache(file)
    if found is not None:
        return found.read_bytes()
    return None


async def _download(url: str, max_bytes: int) -> Optional[bytes]:
    if not url.startswith(("http://", "https://")):
        return None
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    logger.warning(f"舞萌绑定：图片超过 {max_bytes} 字节，已跳过")
                    return None
                chunks.append(chunk)
    return b"".join(chunks)


def has_image(event: MessageEvent) -> bool:
    """消息里有没有图片段；私聊自动识别二维码时用来做前置判断。"""
    return bool(_image_segments(event))


async def first_image_bytes(event: MessageEvent, max_bytes: int) -> Optional[bytes]:
    """取消息里第一张图片的原始字节；没有图片或读取失败返回 None。"""
    for file, url in _image_segments(event):
        data = _read_local(file) if file else None
        if data is None and url:
            try:
                data = await _download(url, max_bytes)
            except Exception as exc:
                logger.warning(f"舞萌绑定：图片下载失败（{type(exc).__name__}）")
                data = None
        if not data:
            continue
        if len(data) > max_bytes:
            logger.warning(f"舞萌绑定：图片超过 {max_bytes} 字节，已跳过")
            continue
        return data
    return None

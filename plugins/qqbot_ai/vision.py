"""图片获取：把 QQ 消息里的图片段转成模型能吃的 data URL。

流程：``image`` 段 → 用 ``file://`` 绝对路径读取（OneBot 实现本地缓存文件）
→ 失败再退回 ``url 字段`` 直接下载 → 按文件头嗅探类型 → base64 data URL。

限制：

- 单张大小超过 ``max_bytes`` 直接跳过；
- 超过 ``max_images`` 张的部分跳过；
- 任一环节失败都返回 ``None``，不影响文字提问。
"""

import base64
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from nonebot.log import logger

# 文件头 -> MIME
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_FILE_URI_RE = re.compile(r"^file://(.+)$")
_DATA_URI_RE = re.compile(r"^data:(image/[\w.+-]+);base64,")

# 协议端（llbot）容器里的图片缓存目录，通过 docker-compose 只读挂进 NoneBot 容器。
# 图片段给的 file:// 路径是协议端容器内的路径，本容器读不到，按文件名回退到这里找。
_LOCAL_CACHE_DIRS = (
    Path("/llbot-data/temp"),
    Path("/llbot-data"),
)


def find_in_local_cache(file_name: str) -> Optional[Path]:
    """按文件名在协议端缓存目录里找图片（找不到返回 None）。

    只允许直接包含在缓存目录下的普通文件，防止被 ``../`` 之类的路径穿越。
    """
    name = Path(file_name).name
    if not name or name in (".", ".."):
        return None
    for base in _LOCAL_CACHE_DIRS:
        if not base.is_dir():
            continue
        candidate = base / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def sniff_mime(data: bytes) -> Optional[str]:
    """按文件头判断图片类型；识别不了返回 ``None``。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def to_data_url(data: bytes) -> Optional[str]:
    mime = sniff_mime(data)
    if mime is None:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _segment_payload(seg: Any) -> Optional[dict[str, Any]]:
    """把 OneBot 的图片段（对象或原始 dict）统一成 ``{"file","url"}``。"""
    if isinstance(seg, dict):
        # 兼容两种写法：{"type":"image","data":{...}} 和 {"type":"image","file":...}
        data = seg.get("data") if isinstance(seg.get("data"), dict) else seg
        stype = seg.get("type")
    else:
        stype = getattr(seg, "type", None)
        data = getattr(seg, "data", None)
    if stype != "image" or not isinstance(data, dict):
        return None
    return {
        "file": str(data.get("file") or ""),
        "url": str(data.get("url") or ""),
    }


def image_sources(message: Any) -> list[dict[str, str]]:
    """从消息里按顺序取出所有 ``image`` 段。"""
    if message is None:
        return []
    segs = message if isinstance(message, list) else (getattr(message, "message", None) or [])
    out: list[dict[str, str]] = []
    for seg in segs:
        payload = _segment_payload(seg)
        if payload is not None:
            out.append(payload)
    return out


async def load_image(
    source: dict[str, str],
    max_bytes: int,
    timeout: float = 10.0,
) -> Optional[str]:
    """把一张图变成 data URL；失败/超大/格式不认识都返回 ``None``。"""
    file_field = source.get("file") or ""
    url_field = source.get("url") or ""

    # 1. 本地缓存文件（NapCat / Lagrange 等会给出 file://）
    candidates: list[str] = []
    if file_field.startswith("file://"):
        candidates.append(file_field)
    m = _FILE_URI_RE.match(url_field)
    if m:
        candidates.append(url_field)
    for uri in candidates:
        path = uri[len("file://"):]
        try:
            with open(path, "rb") as fh:
                data = fh.read(max_bytes + 1)
        except OSError:
            # 跨容器场景：file:// 指向协议端容器内的路径，本容器读不到，
            # 按文件名到挂载进来的缓存目录里再找一次
            cached = find_in_local_cache(file_field or path)
            if cached is None:
                continue
            try:
                with open(cached, "rb") as fh:
                    data = fh.read(max_bytes + 1)
            except OSError:
                continue
        if len(data) > max_bytes:
            logger.info("[qqbot_ai] 图片超过大小上限，已跳过")
            return None
        data_url = to_data_url(data)
        if data_url:
            return data_url

    # 2. 直接是 data URL
    if url_field.startswith("data:"):
        return url_field if len(url_field) <= max_bytes * 2 else None

    # 3. 远程地址：下载（不跟随到非 http(s)）
    if url_field.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url_field)
        except httpx.HTTPError as e:
            logger.info(f"[qqbot_ai] 下载图片失败：{type(e).__name__}")
            return None
        if resp.status_code != 200:
            return None
        if len(resp.content) > max_bytes:
            logger.info("[qqbot_ai] 图片超过大小上限，已跳过")
            return None
        return to_data_url(resp.content)

    return None


async def collect_images(
    message: Any,
    max_images: int,
    max_bytes: int,
) -> list[str]:
    """按顺序把最多 ``max_images`` 张图转成 data URL。"""
    if message is None or max_images <= 0:
        return []
    urls: list[str] = []
    for source in image_sources(message)[:max_images]:
        data_url = await load_image(source, max_bytes)
        if data_url:
            urls.append(data_url)
    return urls

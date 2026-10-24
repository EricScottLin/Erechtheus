"""群精华自动收录。

OneBot v11 没有「精华变更」通知事件，只能定时轮询：

1. ``get_group_list`` 拿机器人所在的群
2. ``get_essence_msg_list`` 拿每群精华元数据（只有 message_id / 发送者等，没有正文）
3. ``get_msg(message_id)`` 取正文
4. 没记录过的精华消息，把纯文本追加进 ``quotes.json``

已处理的 message_id 存在 ``essence_seen.json``，避免重复收录。
"""

import asyncio
import json
from pathlib import Path

from nonebot import get_bots, require
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.log import logger

from . import library
from .config import plugin_config

try:  # apscheduler 缺失时降级为「只支持 /金句同步 手动触发」
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler  # noqa: E402
except Exception:  # noqa: BLE001
    scheduler = None


# 纯图片精华怎么记录：文字标记 + 图片本体另存
IMAGE_QUOTE_PREFIX = "[图片]"


def extract_image_segments(msg: object) -> list[dict]:
    """从 ``get_msg`` 的返回里取所有图片段的 data（含 file / url / file_size）。"""
    if not isinstance(msg, dict):
        return []
    segments = msg.get("message")
    if not isinstance(segments, list):
        return []
    out: list[dict] = []
    for item in segments:
        if not isinstance(item, dict) or item.get("type") not in ("image", "mface"):
            continue
        data = item.get("data") or {}
        if data.get("url") or data.get("file"):
            out.append(dict(data))
    return out


def extract_image_urls(msg: object) -> list[str]:
    """兼容旧接口：只返回图片地址列表。"""
    urls = []
    for data in extract_image_segments(msg):
        for key in ("url", "file"):
            value = str(data.get(key) or "")
            if value.startswith(("http://", "https://", "base64://")):
                urls.append(value)
                break
    return urls


def extract_text(msg: object) -> str:
    """从 ``get_msg`` 的返回里提取纯文本。"""
    if not isinstance(msg, dict):
        return ""
    segments = msg.get("message")
    if isinstance(segments, str):
        # 兼容 CQ 码字符串
        try:
            return Message(segments).extract_plain_text().strip()
        except Exception:  # noqa: BLE001
            return segments.strip()
    if isinstance(segments, list):
        parts: list[str] = []
        for item in segments:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str((item.get("data") or {}).get("text") or ""))
        return "".join(parts).strip()
    return ""


async def sync_once(bot: Bot) -> tuple[int, int]:
    """同步一次，返回 ``(检查群数, 新增金句数)``。"""
    seen = library.load_essence_seen()
    no_image = load_no_image()
    added = 0

    groups = await bot.get_group_list()
    groups = [
        g for g in (groups or []) if isinstance(g, dict) and g.get("group_id")
    ]

    for group in groups:
        group_id = int(group["group_id"])
        existing = set(library.load_quotes(group_id))
        try:
            items = await bot.get_essence_msg_list(group_id=group_id)
        except asyncio.TimeoutError:  # noqa: PERF203
            logger.debug(f"[qqbot_meme] 群 {group_id} 精华列表超时，跳过")
            continue
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 获取群 {group_id} 精华列表失败：{type(e).__name__}: {e}")
            continue

        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                message_id = int(item["message_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if message_id in seen:
                continue
            seen.add(message_id)

            try:
                msg = await bot.get_msg(message_id=message_id)
            except asyncio.TimeoutError:
                logger.debug(f"[qqbot_meme] 精华消息 {message_id} 超时，跳过")
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_meme] 获取精华消息 {message_id} 失败：{e}")
                continue

            text = extract_text(msg)
            image_urls: list[str] = []
            image_segs: list[dict] = []
            if not text:
                # 纯图片/表情精华：用 [图片] 占位，并把图存进表情库
                image_segs = extract_image_segments(msg)
                image_urls = extract_image_urls(msg)
                if not image_urls and not image_segs:
                    continue  # 既没文字也没图片（语音/文件等）才跳过
                text = IMAGE_QUOTE_PREFIX

            author = str(item.get("sender_nick") or "").strip() or None
            group_name = msg.get("group_name") if isinstance(msg, dict) else None
            sender_id = item.get("sender_id")
            author_id = str(sender_id) if sender_id is not None else None

            if text in existing:
                # 早期导入的条目没有 author_id，这里顺带回填
                await library.set_author_by_text(
                    group_id, text, author, author_id
                )
                continue

            await library.append_quote(
                text,
                author=author,
                group_id=group_id,
                group_name=group_name,
                author_id=str(sender_id) if sender_id is not None else None,
            )
            existing.add(text)
            added += 1

            # 纯图片精华：顺手把图片收进表情库（内容哈希去重）
            if image_segs:
                got = await _save_images(bot, image_segs)
                if got <= 0:
                    # 图片已被腾讯 rkey 拦掉、本地也没有缓存：记下来别再重试
                    no_image.add(message_id)

    library.save_essence_seen(seen)
    save_no_image(no_image)
    return len(groups), added


# llbot 容器内的图片缓存目录 → NoneBot 容器里的挂载点（docker-compose 已 ro 挂入）
_LLBOT_IMAGE_ROOTS = {
    "/app/llbot/data": "/llbot-data",
    "/app/llbot": "/llbot-data",
}


def _local_image_path(path: str) -> Path | None:
    """把 llbot 容器内的路径映射到本容器可读的挂载路径。"""
    if not path:
        return None
    pure = Path(path)
    for src, dst in _LLBOT_IMAGE_ROOTS.items():
        if str(pure).startswith(src):
            mapped = Path(dst) / pure.relative_to(src)
            return mapped if mapped.is_file() else None
    return pure if pure.is_file() else None


async def _image_from_onebot(bot: Bot, seg_data: dict) -> bytes | None:
    """让 OneBot 把图片落到本地再读（腾讯 rkey 直连常被拦，这条最稳）。

    注意：LLBot 在图片下载失败时会把**图片 URL** 放在 ``message`` 字段、
    且 ``retcode`` 仍是 0，所以不能只看 retcode，必须校验拿到的确实是本地文件。
    """
    for key in ("file", "url"):
        value = str(seg_data.get(key) or "")
        if not value:
            continue
        try:
            result = await bot.call_api("get_image", file=value)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(result, dict):
            continue
        candidates = [
            str(result.get("file") or ""),
            str(result.get("path") or ""),
            str(result.get("url") or ""),
        ]
        for candidate in candidates:
            if candidate.startswith(("http://", "https://")):
                continue  # 这是下载失败后回吐的 URL，不是本地文件
            local = _local_image_path(candidate)
            if local is not None:
                try:
                    data = local.read_bytes()
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    return data
    return None


def _guess_from_temp(seg_data: dict) -> Path | None:
    """get_image 拿不到时，按文件大小在 llbot 的 temp 目录里找同一张图。"""
    try:
        size = int(seg_data.get("file_size") or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    temp_dirs = [Path("/llbot-data/temp"), Path("/llbot-data/image")]
    for directory in temp_dirs:
        if not directory.is_dir():
            continue
        for path in directory.glob("*"):
            try:
                if path.is_file() and path.stat().st_size == size:
                    return path
            except Exception:  # noqa: BLE001
                continue
    return None


_no_image: set[int] = set()
_NO_IMAGE_FILE_NAME = "essence_no_image.json"


def _no_image_file() -> Path:
    return library.DATA_DIR / _NO_IMAGE_FILE_NAME


def load_no_image() -> set[int]:
    global _no_image
    path = _no_image_file()
    if not path.is_file():
        return _no_image
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _no_image = {int(x) for x in raw} if isinstance(raw, list) else set()
    except Exception:  # noqa: BLE001
        _no_image = set()
    return _no_image


def save_no_image(values: set[int]) -> None:
    global _no_image
    _no_image = values
    path = _no_image_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(values)), encoding="utf-8")


async def _save_images(bot: Bot, segments: list[dict]) -> int:
    """把精华图片存进表情包库，返回新增张数。"""
    saved = 0
    for seg in segments:
        try:
            data = await _image_from_onebot(bot, seg)
            if not data:
                guess = _guess_from_temp(seg)
                if guess is not None:
                    data = guess.read_bytes()
            if not data:
                url = str(seg.get("url") or "")
                if url.startswith(("http://", "https://", "base64://")):
                    data = await _fetch_image(bot, url)
            if not data:
                logger.debug(f"[qqbot_meme] 精华图片获取失败（跳过）：{seg.get('file')}")
                continue
            _, created = library.save_sticker(data)
            saved += int(created)
        except Exception as e:  # noqa: BLE001 —— 单张失败不影响收录
            logger.warning(f"[qqbot_meme] 精华图片入库失败：{e}")
    return saved


async def _fetch_image(bot: Bot, url: str) -> bytes | None:
    """下载图片；兼容 base64 与 HTTP。"""
    if url.startswith("base64://"):
        import base64

        try:
            return base64.b64decode(url[len("base64://") :])
        except Exception:  # noqa: BLE001
            return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            return resp.content
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_meme] 下载精华图片失败：{e}")
    return None


def _interval_seconds() -> int:
    return max(30, int(plugin_config.qqbot_meme_essence_interval))


if scheduler is not None:

    @scheduler.scheduled_job(
        "interval",
        seconds=_interval_seconds(),
        id="qqbot_meme_essence_sync",
        max_instances=1,
    )
    async def _periodic_sync() -> None:
        """定时把新精华收进金句库。"""
        if not plugin_config.qqbot_meme_essence_sync:
            return
        for bot in get_bots().values():
            if bot.adapter.get_name() != "OneBot V11":
                continue
            try:
                groups, added = await sync_once(bot)  # type: ignore[arg-type]
                if added:
                    logger.info(
                        f"[qqbot_meme] 精华同步：检查 {groups} 个群，新增金句 {added} 条"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_meme] 精华同步失败：{type(e).__name__}: {e}")

"""表情包库与金句库的存储层。

数据目录由 ``nonebot_plugin_localstore`` 提供（容器内 ``/app/data/qqbot_meme``，
对应宿主机 ``/srv/qqbot/data/qqbot_meme``）；localstore 缺失时退回
``cwd/data/qqbot_meme``。

目录结构::

    data/qqbot_meme/
    ├── stickers/        # 表情包文件（png/jpg/gif/webp），也可手动丢文件进去
    ├── stickers.json    # 预留：表情包元数据索引（当前未使用，扫描目录即可）
    └── quotes.json      # 金句库：JSON 字符串数组，结构见 README

表情包文件名使用内容 sha1 前 16 位，天然去重。
"""

import asyncio
import hashlib
import json
import random
from pathlib import Path

from nonebot.log import logger

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover - localstore 缺失时退到当前目录
    DATA_DIR = Path.cwd() / "data" / "qqbot_meme"

STICKER_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# 表情包元数据索引：{文件名: {"sha1": ..., "phash": int, "source": ...}}
STICKERS_INDEX_NAME = "stickers.json"

# 金句库文件（预留）：JSON 数组，元素可以是字符串，也可以是 {"text": ..., "author": ...}
QUOTES_FILE_NAME = "quotes.json"
ESSENCE_SEEN_FILE_NAME = "essence_seen.json"

_quote_lock = asyncio.Lock()


# ---------------------------------------------------------------- 路径
def stickers_dir() -> Path:
    return DATA_DIR / "stickers"


def quotes_file() -> Path:
    return DATA_DIR / QUOTES_FILE_NAME


def essence_seen_file() -> Path:
    return DATA_DIR / ESSENCE_SEEN_FILE_NAME


def bootstrap() -> None:
    """确保数据目录与金句库文件存在（幂等）。"""
    stickers_dir().mkdir(parents=True, exist_ok=True)
    if not quotes_file().exists():
        quotes_file().write_text("[]\n", encoding="utf-8")


# ---------------------------------------------------------------- 表情包
def stickers_index_file() -> Path:
    return DATA_DIR / STICKERS_INDEX_NAME


def load_stickers_index() -> dict[str, dict]:
    path = stickers_index_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_meme] 读取表情索引失败：{e}")
        return {}
    return raw if isinstance(raw, dict) else {}


def save_stickers_index(index: dict[str, dict]) -> None:
    path = stickers_index_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def list_stickers() -> list[Path]:
    """按文件名排序列出表情包文件。"""
    directory = stickers_dir()
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in STICKER_EXTS
    )


def random_sticker(max_bytes: int | None = None) -> Path | None:
    """随机取一张表情包；可限制最大字节数（base64 发送会膨胀）。"""
    files = list_stickers()
    if max_bytes is not None:
        files = [p for p in files if p.stat().st_size <= max_bytes]
    return random.choice(files) if files else None


def sniff_ext(data: bytes) -> str | None:
    """按文件头判断真实图片格式（QQ 的图片后缀常与内容不符）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_sticker(data: bytes) -> tuple[Path, bool]:
    """保存表情包，返回 ``(路径, 是否新增)``；相同内容不会重复落盘。"""
    ext = sniff_ext(data)
    if ext is None:
        raise ValueError("unsupported image format")
    digest = hashlib.sha1(data).hexdigest()[:16]
    path = stickers_dir() / f"{digest}{ext}"
    created = not path.exists()
    if created:
        stickers_dir().mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    # 维护索引（便于近似查重）
    try:
        from . import dedup

        index = load_stickers_index()
        entry = index.get(path.name) or {}
        entry["sha1"] = hashlib.sha1(data).hexdigest()
        if entry.get("phash") is None:
            entry["phash"] = dedup.perceptual_hash(data)
        index[path.name] = entry
        save_stickers_index(index)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[qqbot_meme] 更新表情索引失败：{e}")
    return path, created


def find_duplicate(data: bytes) -> tuple[str, Path] | None:
    """检查这张图是否已经在库里。

    返回 ``(命中方式, 已存在的文件)``，命中方式为 ``"same"``（完全一致）
    或 ``"similar"``（近似重复）；未命中返回 ``None``。
    """
    from . import dedup

    new_hash = dedup.content_hash(data)
    new_phash = dedup.perceptual_hash(data)
    index = load_stickers_index()
    files = list_stickers()

    # 1) 完全一致：哈希相同（含旧库里没有索引的文件，现算）
    for path in files:
        try:
            old_bytes = path.read_bytes()
        except Exception:  # noqa: BLE001
            continue
        old_hash = (index.get(path.name) or {}).get("sha1")
        if old_hash is None:
            old_hash = dedup.content_hash(old_bytes)
            entry = index.get(path.name) or {}
            entry["sha1"] = old_hash
            index[path.name] = entry
        if old_hash == new_hash:
            save_stickers_index(index)
            return "same", path

    # 2) 近似一致：感知哈希距离足够近
    if new_phash is not None:
        best: tuple[int, int, Path] | None = None
        for path in files:
            entry = index.get(path.name) or {}
            phash = entry.get("phash")
            if phash is None:
                try:
                    phash = dedup.perceptual_hash(path.read_bytes())
                except Exception:  # noqa: BLE001
                    continue
                entry["phash"] = phash
                index[path.name] = entry
            dist = dedup.distance(new_phash, int(phash))
            if dist <= dedup.SIMILAR_THRESHOLD and (best is None or dist < best[0]):
                best = (dist, int(phash), path)
        save_stickers_index(index)
        if best is not None:
            return "similar", best[2]
    return None


def delete_sticker(index: int) -> Path | None:
    """按列表序号（0 起）删除表情包；越界返回 None。"""
    files = list_stickers()
    if not 0 <= index < len(files):
        return None
    path = files[index]
    path.unlink(missing_ok=True)
    return path


# ---------------------------------------------------------------- 金句库
def _load_raw_quotes() -> list:
    """读取 quotes.json 的原始列表（保留作者/群等扩展字段）。"""
    path = quotes_file()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 —— 坏文件不应让命令崩溃
        logger.warning(f"[qqbot_meme] 读取金句库失败：{e}")
        return []
    if isinstance(raw, dict):
        raw = raw.get("quotes", [])
    return raw if isinstance(raw, list) else []


def _entries_from(raw: list) -> list[dict]:
    """把原始条目统一成 ``{"text", "author"?, "group_id"?, "group_name"?}``。"""
    entries: list[dict] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            entries.append({"text": item.strip()})
            continue
        if not isinstance(item, dict) or not item.get("text"):
            continue
        entry: dict = {"text": str(item["text"]).strip()}
        if item.get("author"):
            entry["author"] = str(item["author"]).strip()
        if item.get("author_id") is not None:
            try:
                entry["author_id"] = str(item["author_id"]).strip()
            except (TypeError, ValueError):
                pass
        if item.get("group_id") is not None:
            try:
                entry["group_id"] = int(item["group_id"])
            except (TypeError, ValueError):
                pass
        if item.get("group_name"):
            entry["group_name"] = str(item["group_name"])
        entries.append(entry)
    return entries


def _entries() -> list[dict]:
    return _entries_from(_load_raw_quotes())


def _filter_group(entries: list[dict], group_id: int | None) -> list[dict]:
    """group_id 为 None 时不过滤；否则只保留本群与无群归属（旧数据）的条目。"""
    if group_id is None:
        return entries
    return [e for e in entries if e.get("group_id") in (None, group_id)]


def load_quotes(group_id: int | None = None) -> list[str]:
    """读取金句文本；``group_id`` 给定时只返回该群（及旧的无归属）的条目。"""
    return [e["text"] for e in _filter_group(_entries(), group_id)]


def random_quote(group_id: int | None = None) -> str | None:
    entry = random_quote_entry(group_id)
    return entry["text"] if entry else None


def random_quote_entry(group_id: int | None = None) -> dict | None:
    """随机取一条金句（含作者/群信息），只在指定群内抽。"""
    entries = _filter_group(_entries(), group_id)
    return random.choice(entries) if entries else None


async def set_author_by_text(
    group_id: int | None,
    text: str,
    author: str | None,
    author_id: str | None,
) -> bool:
    """给已存在但缺 author_id 的条目回填作者信息（早期数据迁移用）。"""
    if not author_id:
        return False
    text = text.strip()
    if not text:
        return False
    async with _quote_lock:
        raw = _load_raw_quotes()
        changed = False
        for item in raw:
            if not isinstance(item, dict):
                continue
            if str(item.get("text") or "").strip() != text:
                continue
            if item.get("author_id"):
                continue
            if group_id is not None and item.get("group_id") not in (None, int(group_id)):
                continue
            item["author_id"] = str(author_id)
            if author:
                item["author"] = author
            changed = True
        if changed:
            path = quotes_file()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(path)
        return changed


def quotes_by_user(
    group_id: int | None, user_id: str, names: list[str] | None = None
) -> list[dict]:
    """查某用户在指定群说过的金句。

    优先按 ``author_id`` 精确匹配；旧数据没有 author_id 时退回群名片/昵称匹配。
    """
    entries = _filter_group(_entries(), group_id)
    uid = str(user_id)
    matched = [e for e in entries if str(e.get("author_id") or "") == uid]
    if not matched and names:
        wanted = {n.strip() for n in names if n and n.strip()}
        if wanted:
            matched = [e for e in entries if e.get("author") in wanted]
    return matched


async def append_quote(
    text: str,
    author: str | None = None,
    group_id: int | None = None,
    group_name: str | None = None,
    author_id: str | None = None,
) -> int:
    """追加一条金句，返回该群的金句总数。

    条目结构：``{"text": ..., "author": ..., "group_id": ..., "group_name": ...}``，
    可选字段有值才写入。
    """
    entry: dict = {"text": text.strip()}
    if author:
        entry["author"] = author
    if author_id:
        entry["author_id"] = str(author_id)
    if group_id is not None:
        entry["group_id"] = int(group_id)
    if group_name:
        entry["group_name"] = group_name

    async with _quote_lock:
        raw = _load_raw_quotes()
        raw.append(entry)
        path = quotes_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return len(_filter_group(_entries_from(raw), group_id))


# ---------------------------------------------------------------- 群精华同步状态
def load_essence_seen() -> set[int]:
    """已收录过的精华消息 id 集合。"""
    path = essence_seen_file()
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_meme] 读取精华同步状态失败：{e}")
        return set()
    if isinstance(raw, dict):
        raw = raw.get("seen", [])
    seen: set[int] = set()
    for item in raw if isinstance(raw, list) else []:
        try:
            seen.add(int(item))
        except (TypeError, ValueError):
            continue
    return seen


def save_essence_seen(seen: set[int]) -> None:
    path = essence_seen_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


bootstrap()

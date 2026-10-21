"""「我说你说」教学库的存储层。

数据目录由 ``nonebot_plugin_localstore`` 提供（容器内 ``/app/data/qqbot_repeat``，
对应宿主机 ``/srv/qqbot/data/qqbot_repeat``）；localstore 缺失时退回
``cwd/data/qqbot_repeat``。

目录结构::

    data/qqbot_repeat/
    ├── teach.json     # 按群保存的教学规则
    └── images/        # 教学用的表情包图片，文件名 = 内容 sha1 前 16 位

``teach.json`` 结构::

    {
      "984616276": {
        "冰冰冰": {"kind": "image", "value": "ab12cd34ef56.png", "by": "10001", "at": 1700000000},
        "你好":   {"kind": "text",  "value": "你好呀",             "by": "10001", "at": 1700000001}
      }
    }

图片单独落盘、按内容哈希命名，原因有两点：

1. QQ 的图片 URL 会过期，教学时就把图片下载下来，之后不再依赖原链接；
2. LLBot 与 NoneBot 是两个容器、文件系统不互通，发送时用 base64
   （``MessageSegment.image(bytes)``），不能传 ``file://`` 路径。
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from nonebot.log import logger

try:
    from nonebot_plugin_localstore import get_plugin_data_dir

    DATA_DIR: Path = get_plugin_data_dir()
except Exception:  # pragma: no cover - localstore 缺失时退回当前目录
    DATA_DIR = Path.cwd() / "data" / "qqbot_repeat"

TEACH_FILE_NAME = "teach.json"
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

_lock = asyncio.Lock()
_cache: dict[str, dict[str, dict]] | None = None
_cache_mtime: float = 0.0


# ---------------------------------------------------------------- 路径
def images_dir() -> Path:
    return DATA_DIR / "images"


def teach_file() -> Path:
    return DATA_DIR / TEACH_FILE_NAME


def bootstrap() -> None:
    """确保数据目录与教学库文件存在（幂等），并清掉内存缓存。"""
    global _cache, _cache_mtime
    images_dir().mkdir(parents=True, exist_ok=True)
    if not teach_file().exists():
        teach_file().write_text("{}\n", encoding="utf-8")
    _cache = None
    _cache_mtime = 0.0


# ---------------------------------------------------------------- 读写
def _normalize(raw: Any) -> dict[str, dict[str, dict]]:
    """把 JSON 内容规整成 ``{群号: {触发词: {kind, value, ...}}}``。"""
    if not isinstance(raw, dict):
        return {}
    data: dict[str, dict[str, dict]] = {}
    for group_id, rules in raw.items():
        if not isinstance(rules, dict):
            continue
        clean: dict[str, dict] = {}
        for trigger, rule in rules.items():
            if not isinstance(trigger, str) or not trigger.strip():
                continue
            if not isinstance(rule, dict):
                continue
            kind = rule.get("kind")
            value = rule.get("value")
            if kind not in ("image", "text") or not isinstance(value, str) or not value:
                continue
            entry: dict = {"kind": kind, "value": value}
            if rule.get("by") is not None:
                entry["by"] = str(rule["by"])
            if rule.get("at") is not None:
                try:
                    entry["at"] = int(rule["at"])
                except (TypeError, ValueError):
                    pass
            clean[trigger] = entry
        data[str(group_id)] = clean
    return data


def _load() -> dict[str, dict[str, dict]]:
    """读取教学库；文件没变时直接返回内存缓存。"""
    global _cache, _cache_mtime
    path = teach_file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cache = {}
        _cache_mtime = 0.0
        return _cache
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 —— 坏文件不应让命令崩溃
        logger.warning(f"[qqbot_repeat] 读取教学库失败：{e}")
        raw = {}
    _cache = _normalize(raw)
    _cache_mtime = mtime
    return _cache


def _save(data: dict[str, dict[str, dict]]) -> None:
    global _cache, _cache_mtime
    path = teach_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    _cache = data
    try:
        _cache_mtime = path.stat().st_mtime
    except OSError:  # pragma: no cover - 极端情况下文件刚被删
        _cache_mtime = 0.0


# ---------------------------------------------------------------- 查询
def list_rules(group_id: int) -> list[tuple[str, dict]]:
    """返回本群规则 ``[(触发词, 规则)]``，按教学先后排序。"""
    return list(_load().get(str(group_id), {}).items())


def count_rules(group_id: int) -> int:
    return len(_load().get(str(group_id), {}))


def get_rule(group_id: int, trigger: str) -> dict | None:
    return _load().get(str(group_id), {}).get(trigger)


def find_rule(
    group_id: int, text: str, match: str = "exact"
) -> tuple[str, dict] | None:
    """按触发方式找规则。

    - ``exact``：整句完全相同（忽略首尾空白）
    - ``contains``：消息包含触发词；多个命中时取最长的触发词
    """
    rules = _load().get(str(group_id), {})
    if not rules:
        return None
    text = (text or "").strip()
    if not text:
        return None
    if match == "contains":
        for trigger in sorted(rules, key=len, reverse=True):
            if trigger and trigger in text:
                return trigger, rules[trigger]
        return None
    return (text, rules[text]) if text in rules else None


# ---------------------------------------------------------------- 修改
async def upsert_rule(
    group_id: int,
    trigger: str,
    kind: str,
    value: str,
    by: str,
    max_rules: int,
) -> bool:
    """新增或覆盖一条规则，返回是否覆盖了旧规则。

    新规则超过 ``max_rules`` 时抛 ``ValueError``（调用方转成用户提示）。
    """
    async with _lock:
        data = _load()
        group = data.setdefault(str(group_id), {})
        replaced = trigger in group
        if not replaced and len(group) >= max_rules:
            raise ValueError("rule limit reached")
        group[trigger] = {
            "kind": kind,
            "value": value,
            "by": str(by),
            "at": int(time.time()),
        }
        _save(data)
        return replaced


async def delete_rule(group_id: int, trigger: str) -> bool:
    """删除一条规则；不存在返回 False。"""
    async with _lock:
        data = _load()
        group = data.get(str(group_id))
        if not group or trigger not in group:
            return False
        group.pop(trigger, None)
        if not group:
            data.pop(str(group_id), None)
        _save(data)
        return True


# ---------------------------------------------------------------- 图片
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


def save_image(data: bytes) -> str:
    """保存教学图片，返回文件名；相同内容不会重复落盘。"""
    ext = sniff_ext(data)
    if ext is None:
        raise ValueError("unsupported image format")
    name = f"{hashlib.sha1(data).hexdigest()[:16]}{ext}"
    path = images_dir() / name
    if not path.exists():
        images_dir().mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return name


def image_path(name: str) -> Path | None:
    """把规则里的图片文件名解析成路径；非法文件名返回 None。"""
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in IMAGE_EXTS:
        return None
    return images_dir() / name


bootstrap()

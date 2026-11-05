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
    """读取索引：``{trigger: [file, ...]}``（别名已展开）。

    磁盘上每个触发词的原始结构可能是：
      * ``[file, ...]``                                  老格式，无别名
      * ``{"files": [...], "aliases": [...]}``           新格式，带别名

    别名在读取时双向展开：A 的别名是 B，则 B 的别名里也包含 A，
    两边查到的图合并成同一个集合。
    """
    raw = _load_raw()
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        files = value["files"] if isinstance(value, dict) else value
        if isinstance(files, list):
            out.setdefault(str(key), []).extend(str(f) for f in files)

    # 别名图：trigger -> 关联的其他 trigger
    graph: dict[str, set[str]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        aliases = [str(a).strip() for a in value.get("aliases") or [] if str(a).strip()]
        for alias in aliases:
            graph.setdefault(str(key), set()).add(alias)
            graph.setdefault(alias, set()).add(str(key))  # 双向

    for key in out:
        graph.setdefault(key, set())
    for key in graph:
        out.setdefault(key, [])

    # 每个触发词合并其连通分量里的所有图
    result: dict[str, list[str]] = {}
    for key in out:
        seen: set[str] = set()
        stack = [key]
        merged: list[str] = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            merged.extend(out.get(cur, []))
            stack.extend(graph.get(cur, ()))
        result[key] = list(dict.fromkeys(merged))
    return result


def _load_raw() -> dict:
    """读取原始索引结构（不做别名展开）。"""
    if not INDEX_FILE.is_file():
        return {}
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_roast] 读取梗图索引失败：{e}")
        return {}
    if not isinstance(raw, dict):
        return {}

    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, list):
            files = [str(f) for f in value]
            if files:
                out.setdefault(str(key), [])
                out[str(key)] = list(dict.fromkeys(out[str(key)] + files))
        elif isinstance(value, dict) and "files" in value:
            entry = {
                "files": [str(f) for f in value.get("files") or []],
                "aliases": [str(a) for a in value.get("aliases") or []],
            }
            out[str(key)] = entry
        elif isinstance(value, dict):  # 旧结构：按群
            for trigger, files in value.items():
                if not isinstance(files, list):
                    continue
                out.setdefault(str(trigger), [])
                out[str(trigger)] = list(dict.fromkeys(out[str(trigger)] + [str(f) for f in files]))
    return out


def _sel(raw: dict, trigger: str) -> dict:
    """取某个触发词的原始条目，统一成 ``{"files": [...], "aliases": [...]}``。"""
    value = raw.get(trigger)
    if isinstance(value, dict):
        return {
            "files": list(value.get("files") or []),
            "aliases": list(value.get("aliases") or []),
        }
    if isinstance(value, list):
        return {"files": list(value), "aliases": []}
    return {"files": [], "aliases": []}


def _put(raw: dict, trigger: str, entry: dict) -> None:
    """写回：没有别名且只有图时保持老格式，减少 diff 噪音。"""
    if not entry["aliases"]:
        raw[trigger] = entry["files"]
    else:
        raw[trigger] = {"files": entry["files"], "aliases": entry["aliases"]}


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
        raw = _load_raw()
        entry = _sel(raw, trigger)
        # 若是往「已合并的别名」里加图，记下这张图属于别名本身，
        # 这样取消别名时能原样还原，不会把图留在主词下
        known = [k for k in raw if k != trigger and name in _sel(raw, k)["files"]]
        if name in entry["files"] and known:
            split = _load_split()
            split[trigger] = list(dict.fromkeys(split.get(trigger, []) + [name]))
            _save_split(split)
        is_new = name not in entry["files"]
        if is_new:
            MEMES_DIR.mkdir(parents=True, exist_ok=True)
            image_path(name).write_bytes(image)
            entry["files"].append(name)
        _put(raw, trigger, entry)
        _save(raw)
    return name, is_new


async def remove_meme(trigger: str, name: str | None = None) -> int:
    """删除某个触发词的图，返回删除数量。

    ``name`` 为空时删除该触发词的所有图；给了文件名则只删那一张。
    """
    trigger = trigger.strip()
    async with _lock:
        raw = _load_raw()
        entry = _sel(raw, trigger)
        files = entry["files"]
        if not files:
            return 0
        targets = [name] if name else list(files)
        removed = 0
        for target in targets:
            if target not in files:
                continue
            try:
                image_path(target).unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_roast] 删除梗图 {target} 失败：{e}")
            files.remove(target)
            removed += 1
        if files or entry["aliases"]:
            entry["files"] = files
            _put(raw, trigger, entry)
        else:
            raw.pop(trigger, None)
        _save(raw)
        return removed


def find_name_by_prefix(trigger: str, prefix: str) -> list[str]:
    """按文件名前缀（哈希前几位）查找触发词下的图。"""
    prefix = prefix.strip().lower()
    if not prefix:
        return []
    return [f for f in memes_for(trigger) if f.lower().startswith(prefix)]


def triggers_of() -> dict[str, list[str]]:
    return dict(_load())


def all_triggers() -> list[str]:
    return sorted(_load().keys(), key=len, reverse=True)


def triggers_containing(image: bytes) -> list[str]:
    """反查这张图（按内容哈希命名）已经挂在哪些触发词下。"""
    digest = hashlib.sha1(image).hexdigest()
    name = f"{digest[:16]}{_sniff_ext(image)}"
    data = _load()
    return [trigger for trigger, files in data.items() if name in files]


def memes_for(trigger: str) -> list[str]:
    return list(_load().get(trigger.strip()) or [])


# ---------------------------------------------------------------- 别名
def aliases_of(trigger: str) -> list[str]:
    """某个触发词直接登记的别名。"""
    entry = _sel(_load_raw(), trigger.strip())
    return list(entry["aliases"])


async def add_alias(trigger: str, alias: str) -> tuple[str, str, bool]:
    """给 trigger 添加别名（双向）。

    返回 ``(主触发词, 被合并的触发词, 是否新建别名)``：
      * 两个词都还没登记过：创建主词并把 alias 挂过去；
      * alias 已经是某个词的别名：直接复用那个主词；
      * alias 自己带有图（独立触发词）：把两边的图与别名合并成一个集合。
    """
    trigger = trigger.strip()
    alias = alias.strip()
    if not trigger or not alias or trigger == alias:
        raise ValueError("触发词和别名不能为空，也不能相同")

    async with _lock:
        raw = _load_raw()
        expanded = _load()
        # alias 若属于某个已有集合，就把 trigger 挂进同一个集合
        alias_owner = None
        for key, files in expanded.items():
            if key == trigger:
                continue
            if alias in _aliases_in_component(raw, key) or key == alias:
                alias_owner = key
                break

        target = alias_owner or trigger
        entry = _sel(raw, target)
        is_new = alias not in entry["aliases"]

        # alias 自己带图：合并进主集合
        alias_entry = _sel(raw, alias) if alias != target else {"files": [], "aliases": []}
        for f in alias_entry["files"]:
            if f not in entry["files"]:
                entry["files"].append(f)
        for a in alias_entry["aliases"]:
            if a not in entry["aliases"]:
                entry["aliases"].append(a)
        if is_new:
            entry["aliases"].append(alias)
        # trigger 若是独立存在的词，也把它的图并进来
        trigger_entry = _sel(raw, trigger) if trigger != target else {"files": [], "aliases": []}
        for f in trigger_entry["files"]:
            if f not in entry["files"]:
                entry["files"].append(f)

        _put(raw, target, entry)
        if alias != target:
            raw.pop(alias, None)
            if alias_entry["files"]:
                # 记下别名自己原本带的图，取消别名时好还原
                _SPLIT_FILE.parent.mkdir(parents=True, exist_ok=True)
                split = _load_split()
                split[alias] = list(alias_entry["files"])
                _save_split(split)
        if trigger != target:
            raw.pop(trigger, None)
        _save(raw)
        return target, alias, is_new


_SPLIT_FILE = DATA_DIR / "alias_split.json"


def _load_split() -> dict[str, list[str]]:
    if not _SPLIT_FILE.is_file():
        return {}
    try:
        raw = json.loads(_SPLIT_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {str(k): [str(f) for f in v] for k, v in raw.items() if isinstance(v, list)}


def _save_split(data: dict[str, list[str]]) -> None:
    tmp = _SPLIT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_SPLIT_FILE)


def _images_owned_by(raw: dict, alias: str) -> list[str]:
    split = _load_split()
    return list(split.get(alias) or [])


def _aliases_in_component(raw: dict, key: str) -> set[str]:
    """某个触发词所在别名连通分量里的所有词。"""
    graph: dict[str, set[str]] = {}
    for k, v in raw.items():
        entry = _sel(raw, k)
        for a in entry["aliases"]:
            graph.setdefault(k, set()).add(a)
            graph.setdefault(a, set()).add(k)
    seen: set[str] = set()
    stack = [key]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, ()))
    return seen


async def remove_alias(trigger: str, alias: str) -> bool:
    """删除别名；图不会被删。返回是否真的删除了。"""
    trigger, alias = trigger.strip(), alias.strip()
    async with _lock:
        raw = _load_raw()
        entry = _sel(raw, trigger)
        if alias not in entry["aliases"]:
            # 别名可能在别的词下登记
            for key in list(raw):
                other = _sel(raw, key)
                if alias in other["aliases"]:
                    other["aliases"].remove(alias)
                    _put(raw, key, other)
                    _save(raw)
                    return True
            return False
        entry["aliases"].remove(alias)
        # 别名原本是独立触发词（自己带图）：拆出来单独登记，图不丢
        if alias not in raw:
            split = _load_split()
            alias_files = list(split.pop(alias, []) or [])
            alias_files = [f for f in alias_files if f in entry["files"]]
            if alias_files:
                raw[alias] = alias_files
                _save_split(split)
        _put(raw, trigger, entry)
        _save(raw)
        return True


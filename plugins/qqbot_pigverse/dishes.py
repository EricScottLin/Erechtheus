"""吃什么 / 喝什么：本地菜谱、饮品与连锁店库，随机推荐。

数据文件在 localstore 数据目录：
- dishes.json         索引 [{name, cuisine, file, src_title}]（下厨房菜谱，带图）
- dishes/*.jpg        菜品图片（720x540）
- food_extra.json     连锁店菜单 {chains:{店名:[菜品]}, drinks:{分类:[饮品]}}

连锁店是纯文字菜单（不带图）：下厨房只有「复刻」菜谱，用作官方菜品会误导。
索引缺失时返回空，命令侧给出友好提示；重新抓取见 tools/fetch_dishes.py。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from nonebot.log import logger

from .store import DATA_FILE

DISHES_DIR = DATA_FILE.parent / "dishes"
INDEX_FILE = DATA_FILE.parent / "dishes.json"
EXTRA_FILE = DATA_FILE.parent / "food_extra.json"

_cache: list[dict[str, Any]] | None = None
_extra: dict[str, Any] | None = None


def _load() -> list[dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    if INDEX_FILE.exists():
        try:
            raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                _cache = [d for d in raw if isinstance(d, dict)]
                return _cache
            logger.warning(f"[pigverse] {INDEX_FILE} 结构异常，已重置")
        except Exception as e:
            logger.warning(f"[pigverse] 读取菜品索引失败：{e}，已重置")
    _cache = []
    return _cache


def reload() -> int:
    global _cache
    _cache = None
    return len(_load())


def count() -> int:
    return len(_load())


def cuisines() -> dict[str, int]:
    stats: dict[str, int] = {}
    for d in _load():
        key = str(d.get("cuisine") or "其他")
        stats[key] = stats.get(key, 0) + 1
    return dict(sorted(stats.items(), key=lambda kv: -kv[1]))


def random_dish(cuisine: str = "") -> dict[str, Any] | None:
    items = _load()
    cuisine = normalize(cuisine)
    if cuisine:
        items = [d for d in items if str(d.get("cuisine") or "") == cuisine]
        if not items:
            return None
    if not items:
        return None
    dish = dict(random.choice(items))
    path = DISHES_DIR / str(dish.get("file") or "")
    dish["path"] = path if path.is_file() else None
    return dish


def _load_extra() -> dict[str, Any]:
    """连锁店菜单 + 饮品库。"""
    global _extra
    if _extra is not None:
        return _extra
    if EXTRA_FILE.exists():
        try:
            raw = json.loads(EXTRA_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _extra = raw
                return _extra
            logger.warning(f"[pigverse] {EXTRA_FILE} 结构异常，已重置")
        except Exception as e:
            logger.warning(f"[pigverse] 读取连锁店/饮品数据失败：{e}，已重置")
    _extra = {"chains": {}, "drinks": {}}
    return _extra


def reload_all() -> tuple[int, int]:
    global _cache, _extra
    _cache = None
    _extra = None
    return len(_load()), len(_load_extra().get("chains") or {})


def chains() -> dict[str, int]:
    """连锁店 -> 菜单条目数。"""
    data = _load_extra().get("chains") or {}
    return {str(k): len(v) for k, v in data.items()}


def chain_menu(store: str) -> list[str]:
    data = _load_extra().get("chains") or {}
    for name, items in data.items():
        if name == store or store in name or name in store:
            return list(items)
    return []


def drink_categories() -> dict[str, int]:
    data = _load_extra().get("drinks") or {}
    return {str(k): len(v) for k, v in data.items()}


def random_drink(category: str = "") -> dict[str, Any] | None:
    """随机饮品；category 精确匹配分类/品牌，空则全库随机。"""
    data = _load_extra().get("drinks") or {}
    category = normalize(category)
    if not category:
        pools = [(str(k), list(v)) for k, v in data.items()]
    elif category in data:
        pools = [(category, list(data[category]))]
    else:
        return None
    if not pools:
        return None
    cat, items = random.choice(pools)
    return {"name": random.choice(items), "category": cat}


def random_store_item(store: str) -> dict[str, Any] | None:
    """在指定连锁店里随机一道菜（纯文字，无图片）。"""
    data = _load_extra().get("chains") or {}
    store = normalize(store)
    items = data.get(store)
    if not items:
        return None
    return {"name": random.choice(list(items)), "store": store}


# 用户常见后缀：「麦当劳吃什么」「奶茶喝什么」「瑞幸咖啡呢」——去掉后再精确匹配
_TRAILING_WORDS = (
    "吃什么", "喝什么", "要吃啥", "要喝啥", "吃点啥", "喝点啥",
    "吃点", "喝的", "吃的", "那我", "的话", "呢", "啊", "吧", "好",
)


def normalize(sub: str) -> str:
    """去掉语气词与常见后缀，便于精确匹配。"""
    text = (sub or "").strip()
    changed = True
    while changed and text:
        changed = False
        for word in _TRAILING_WORDS:
            if text.endswith(word) and len(text) > len(word):
                text = text[: -len(word)].strip()
                changed = True
                break
    return text.strip()


def find_target(sub: str) -> tuple[str, str] | None:
    """解析用户输入，返回 (类型, 名称)。

    类型：cuisine（菜系）/ store（连锁店）/ drink（饮品分类或品牌）。
    只做精确匹配（含去后缀后的精确匹配）——模糊纠错交给 AI，
    避免「奶茶」被「奶茶店」这类包含关系误判。
    """
    text = normalize(sub)
    if not text:
        return None
    groups = (
        ("cuisine", cuisines()),
        ("store", chains()),
        ("drink", drink_categories()),
    )
    for kind, mapping in groups:
        if text in mapping:
            return (kind, text)
    return None

"""小猪稀有度体系（Pigverse 设计规范 §2）。

rollpig 的抽取是均匀随机的，原生没有稀有度；这里按「猪的类别」派生：
  UR  私有资源猪（本部署独有 GIF）+ human 人类彩蛋
  SR  EX 差分变体猪（资源包 variant_pigs 声明）
  R   食物猪（pig_rules.json food_pigs）
  N   其余常规猪

数据全部动态读取，不硬编码 230 条映射。
"""

from __future__ import annotations

from functools import lru_cache

from nonebot.log import logger

RARITY_ORDER = {"N": 0, "R": 1, "SR": 2, "UR": 3}
RARITY_LABEL = {"N": "普通", "R": "稀有", "SR": "史诗", "UR": "传说"}
RARITY_COLOR = {"N": "#9aa4af", "R": "#4f9cf9", "SR": "#b46cf0", "UR": "#f5b731"}

# human 彩蛋：pig_rules.json 里声明为 human_pigs 的猪（当前仅 "human"）
_HUMAN_FALLBACK = {"human"}


@lru_cache(maxsize=1)
def _load_rules() -> dict:
    try:
        from nonebot_plugin_rollpig_plus.resource_manager import (
            BUILTIN_RULES_JSON,
        )

        if BUILTIN_RULES_JSON.exists():
            raw = BUILTIN_RULES_JSON.read_text(encoding="utf-8")
            import json

            return json.loads(raw)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[pigverse] 读取 pig_rules.json 失败：{e}")
    return {}


def _food_ids() -> set[str]:
    return set(_load_rules().get("food_pigs", []) or [])


def _human_ids() -> set[str]:
    return set(_load_rules().get("human_pigs", []) or []) or _HUMAN_FALLBACK


@lru_cache(maxsize=1)
def _private_pig_ids() -> set[str]:
    """私有资源猪 id：资源管理器将私有 overlay 与官方包分开记录。"""
    try:
        from nonebot_plugin_rollpig_plus import resource_manager as rm

        ids: set[str] = set()
        # 私有资源目录中存在的 image 文件名（去扩展名）即私有猪 id 线索
        private_dir = getattr(rm, "PRIVATE_RESOURCE_DIR", None)
        if private_dir is not None and private_dir.exists():
            for p in private_dir.rglob("*.gif"):
                ids.add(p.stem)
        return ids
    except Exception:  # pragma: no cover
        return set()


@lru_cache(maxsize=1)
def _variant_pig_ids() -> set[str]:
    try:
        from nonebot_plugin_rollpig_plus import resource_manager as rm

        mgr = rm.pig_resource_manager
        # 资源管理器把变体猪声明保存在内部映射中；无公开 API 时退化为空集
        for attr in ("variant_pig_ids", "variant_pigs", "ex_variant_pig_ids"):
            value = getattr(mgr, attr, None)
            if value:
                return set(value)
        return set()
    except Exception:  # pragma: no cover
        return set()


def rarity_of(pig_id: str) -> str:
    """按类别返回稀有度；未知 id 按普通处理。"""
    if pig_id in _human_ids() or pig_id in _private_pig_ids():
        return "UR"
    if pig_id in _variant_pig_ids():
        return "SR"
    if pig_id in _food_ids():
        return "R"
    return "N"

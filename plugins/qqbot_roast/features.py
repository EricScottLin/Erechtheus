"""把「骂/吐槽」「电」注册进 qqbot_group 的按群功能开关（默认关闭）。"""

import nonebot
from nonebot.log import logger

_KEYS = (
    ("roast", "骂/吐槽", ("骂", "吐槽", "锐评", "嘴臭")),
    ("dian", "电猪", ("电", "电猪", "电他")),
)
_registered = False


def _registry():
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return None
    return getattr(plugin.module, "features", None)


def ensure_registered() -> None:
    global _registered
    if _registered:
        return
    registry = _registry()
    if registry is None:
        return
    try:
        for key, name, aliases in _KEYS:
            registry.register_feature(key, name, default=False, aliases=aliases)
        _registered = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_roast] 注册按群功能开关失败：{e}")


def is_enabled(group_id: int, key: str = "roast", default: bool = False) -> bool:
    ensure_registered()
    registry = _registry()
    if registry is None:
        return default
    try:
        return bool(registry.is_enabled(group_id, key))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_roast] 读取功能开关 {key} 失败：{e}")
        return default


ensure_registered()

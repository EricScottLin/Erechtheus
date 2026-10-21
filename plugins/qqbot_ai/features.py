"""把「小e」注册进 qqbot_group 的按群功能开关（默认开启）。

群里可以用 ``/关闭 小e`` / ``/开启 小e`` 控制，状态存于
``qqbot_group/features.json``；未加载 qqbot_group 时视为开启。
"""

import nonebot
from nonebot.log import logger

_KEY = "ai"
_NAME = "小e"
_ALIASES = ("小e", "ai", "AI", "问答", "AI问答")
_registered = False


def _registry():
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return None
    return getattr(plugin.module, "features", None)


def ensure_registered() -> None:
    """幂等注册；qqbot_group 晚加载时由 ``is_enabled`` 补注册。"""
    global _registered
    if _registered:
        return
    registry = _registry()
    if registry is None:
        return
    try:
        registry.register_feature(_KEY, _NAME, default=True, aliases=_ALIASES)
        _registered = True
        logger.info("[qqbot_ai] 已注册按群功能开关：小e")
    except Exception as e:  # noqa: BLE001 —— 开关注册失败不影响问答
        logger.warning(f"[qqbot_ai] 注册按群功能开关失败：{e}")


def is_enabled(group_id: int, default: bool = True) -> bool:
    ensure_registered()
    registry = _registry()
    if registry is None:
        return default
    try:
        return bool(registry.is_enabled(int(group_id), _KEY))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_ai] 读取功能开关失败：{e}")
        return default


ensure_registered()

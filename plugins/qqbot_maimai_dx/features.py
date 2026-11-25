"""把「科技」注册进 qqbot_group 的按群功能开关（默认开启）。

两层控制，缺一不可：
1. 群必须在开通范围内（``MAIMAI_DX_GROUP_IDS``，见 config.py）；
2. 群里还要开着「科技」（``/开启 科技`` / ``/关闭 科技``）。

帮助图里不展示这个开关标签，只有 ``/本群功能`` 会列出来。
"""

import nonebot
from nonebot.log import logger

KEY = "keji"
NAME = "科技"
ALIASES = ("舞萌直连", "科技功能")
_registered = False


def _registry():
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return None
    return getattr(plugin.module, "features", None)


def ensure_registered() -> None:
    """幂等注册；qqbot_group 晚加载时由 is_enabled 补注册。"""
    global _registered
    if _registered:
        return
    registry = _registry()
    if registry is None:
        return
    try:
        registry.register_feature(KEY, NAME, default=True, aliases=ALIASES)
        _registered = True
        logger.info("[qqbot_maimai_dx] 已注册按群功能开关：科技")
    except Exception as e:  # noqa: BLE001 —— 开关注册失败不影响插件本身
        logger.warning(f"[qqbot_maimai_dx] 注册按群功能开关失败：{e}")


def is_enabled(group_id: int, default: bool = True) -> bool:
    """某群是否开着「科技」；没有 qqbot_group 时按 ``default``。"""
    ensure_registered()
    registry = _registry()
    if registry is None:
        return default
    try:
        return bool(registry.is_enabled(int(group_id), KEY))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_maimai_dx] 读取「科技」开关失败：{e}")
        return default

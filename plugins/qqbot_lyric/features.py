"""可选地接入 ``qqbot_group`` 的按群功能开关（key = ``lyric``，默认关闭）。

默认关闭的理由：这个功能每条像歌词的消息都会调一次模型、还会主动发言，
先让群主自己开 —— ``/开启 歌词``。没有 ``qqbot_group`` 时按 ``default`` 走。
"""

import nonebot
from nonebot.log import logger

_KEY = "lyric"
_NAME = "歌词接龙"
_ALIASES = ("接歌词", "歌词", "接龙", "歌词接龙")

_registered = False


def _registry():
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return None
    return getattr(plugin.module, "features", None)


def ensure_registered() -> None:
    """幂等注册（qqbot_group 晚加载时由 is_enabled 补注册）。"""
    global _registered
    if _registered:
        return
    registry = _registry()
    if registry is None:
        return
    try:
        registry.register_feature(_KEY, _NAME, default=False, aliases=_ALIASES)
        _registered = True
        logger.info(f"[qqbot_lyric] 已注册按群功能开关：{_NAME}（默认关闭）")
    except Exception as e:  # noqa: BLE001 —— 开关注册失败不影响插件本身
        logger.warning(f"[qqbot_lyric] 注册按群功能开关失败：{e}")


def is_enabled(group_id: int, default: bool = False) -> bool:
    ensure_registered()
    registry = _registry()
    if registry is None:
        return default
    try:
        return bool(registry.is_enabled(group_id, _KEY))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_lyric] 读取功能开关失败：{e}")
        return default


def set_enabled(group_id: int, enabled: bool) -> bool:
    ensure_registered()
    registry = _registry()
    if registry is None:
        return False
    try:
        return bool(registry.set_enabled(group_id, _KEY, enabled))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_lyric] 设置功能开关失败：{e}")
        return False

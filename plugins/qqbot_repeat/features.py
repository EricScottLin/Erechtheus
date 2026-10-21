"""可选地接入 ``qqbot_group`` 的按群功能开关。

复读插件已经独立，但仍复用群管插件那套「/本群功能」「/关闭 复读」：

- 如果 ``qqbot_group`` 已加载：把 ``repeat`` / ``teach`` 注册进去，
  开关状态由 ``qqbot_group/features.json`` 统一管理；
- 如果没加载：视为默认开启，插件仍可独立工作。
"""

import nonebot
from nonebot.log import logger

# (key, 显示名, 别名)
_REPEAT = ("repeat", "复读", ("复读机",))
_TEACH = ("teach", "我说你说", ("复读教学", "教学", "教我说话", "我说你学"))

_registered = False


def _registry():
    """取 qqbot_group 的 features 模块；没加载则返回 None。"""
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return None
    return getattr(plugin.module, "features", None)


def ensure_registered() -> None:
    """把本插件的功能注册进 qqbot_group（幂等，插件晚加载也能补注册）。"""
    global _registered
    if _registered:
        return
    registry = _registry()
    if registry is None:
        return
    try:
        registry.register_feature(_REPEAT[0], _REPEAT[1], aliases=_REPEAT[2])
        registry.register_feature(_TEACH[0], _TEACH[1], aliases=_TEACH[2])
        _registered = True
    except Exception as e:  # noqa: BLE001 —— 开关注册失败不应影响复读本身
        logger.warning(f"[qqbot_repeat] 注册按群功能开关失败：{e}")


def is_enabled(group_id: int, key: str, default: bool = True) -> bool:
    """查询某群功能是否开启；没有 qqbot_group 时按 ``default``。"""
    ensure_registered()
    registry = _registry()
    if registry is None:
        return default
    try:
        return bool(registry.is_enabled(group_id, key))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_repeat] 读取功能开关 {key} 失败：{e}")
        return default


ensure_registered()

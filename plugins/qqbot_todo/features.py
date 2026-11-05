"""把「待办」注册进 qqbot_group 的按群功能开关（默认关闭）。

和 qqbot_roast / qqbot_repeat 一样，群聊是否能用待办由按群开关控制：

    /开启 待办        # 在这个群启用
    /关闭 待办
    /本群功能         # 查看

总闸 ``qqbot_todo_group_enabled``（.env.prod）默认改为 True，
两级都满足才在群里生效：``总闸开着 and 本群开关开着``。
私聊不受影响。
"""

import nonebot
from nonebot.log import logger

KEY = "todo"
NAME = "待办"
ALIASES = ("待办", "todo", "TODO", "待办清单", "todo清单")
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
        registry.register_feature(KEY, NAME, default=False, aliases=ALIASES)
        _registered = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_todo] 注册按群功能开关失败：{e}")


def is_enabled(group_id: int, default: bool = False) -> bool:
    """本群是否开启了待办；qqbot_group 未加载时退回 default。"""
    ensure_registered()
    registry = _registry()
    if registry is None:
        return default
    try:
        return bool(registry.is_enabled(int(group_id), KEY))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_todo] 读取待办开关失败：{e}")
        return default


# 插件加载时就注册进 qqbot_group 的开关表；否则 /开启 待办、/本群功能 里
# 都看不到「待办」（resolve_feature 找不到），和 repeat / roast 保持一致。
# 如果 qqbot_group 晚于本插件加载，is_enabled() 内部还会再补注册一次。
ensure_registered()

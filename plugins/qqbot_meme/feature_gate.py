"""读取 qqbot_group 的按群功能开关（插件未加载时视为开启）。"""

import nonebot


def is_enabled(group_id: int | None, key: str, default: bool = True) -> bool:
    if group_id is None:
        return default
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return default
    features = getattr(plugin.module, "features", None)
    if features is None:
        return default
    try:
        return bool(features.is_enabled(int(group_id), key))
    except Exception:  # noqa: BLE001 —— 开关系统异常不应阻断功能
        return default

"""运行时设置：bot 主私聊可改的开关。

优先级：settings.json（运行时改） > Config（环境变量默认值）。

当前支持的键：
  claim_enabled   /领金币开关（True/False）
"""

from typing import Any

from . import storage

KEYS = ("claim_enabled",)


async def get(key: str, default: Any = None) -> Any:
    if key not in KEYS:
        raise KeyError(f"未知设置项：{key}")
    settings = await storage.read_settings()
    return settings.get(key, default)


async def get_claim_enabled(config_enabled: bool) -> bool:
    """活动开关：运行时覆盖优先，否则用 Config 默认值。"""
    override = await get("claim_enabled", None)
    return config_enabled if override is None else bool(override)


async def set(key: str, value: Any) -> None:
    if key not in KEYS:
        raise KeyError(f"未知设置项：{key}")
    await storage.write_setting(key, value)

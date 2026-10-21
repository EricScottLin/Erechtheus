"""「我说你说」的权限判定。

默认只有群主 / 群管 / 机器人超级用户 / 配置的额外管理员能教和删。
额外管理员会合并 ``qqbot_repeat_extra_admins`` 与
``qqbot_group_extra_admins``（群管插件已加载时），避免两处各配一份。
"""

import nonebot
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from .config import plugin_config

ADMIN_ROLES = frozenset({"owner", "admin"})


def _extra_admins() -> set[int]:
    values = set(plugin_config.qqbot_repeat_extra_admins)
    try:
        plugin = nonebot.get_plugin("qqbot_group")
        if plugin is not None:
            group_config = plugin.module.config.plugin_config
            values |= set(group_config.qqbot_group_extra_admins)
    except Exception:  # noqa: BLE001 —— 群管插件异常不应影响权限判断
        pass
    return values


def can_teach(bot: Bot, event: GroupMessageEvent) -> bool:
    """发送者是否有教学/删除权限。"""
    if not plugin_config.qqbot_repeat_teach_admin_only:
        return True
    role = getattr(getattr(event, "sender", None), "role", None)
    if role in ADMIN_ROLES:
        return True
    uid = event.get_user_id()
    superusers = getattr(getattr(bot, "config", None), "superusers", set())
    if uid in {str(x) for x in superusers}:
        return True
    try:
        return int(uid) in _extra_admins()
    except (TypeError, ValueError):
        return False

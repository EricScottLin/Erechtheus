"""表情包管理的权限判定（群主 / 群管 / 机器人超级用户）。"""

from nonebot.adapters.onebot.v11 import Bot, MessageEvent


def is_admin(bot: Bot, event: MessageEvent) -> bool:
    """发送者是否有管理权限（用于添加/删除表情、收录金句）。"""
    if event.get_user_id() in {str(u) for u in bot.config.superusers}:
        return True
    role = getattr(getattr(event, "sender", None), "role", None)
    return role in ("owner", "admin")

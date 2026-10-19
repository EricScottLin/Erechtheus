"""群管插件的纯逻辑：目标解析、时长解析/格式化、权限判定。

本模块不依赖 Bot 连接，可单独导入做单元测试。
"""

import random
import re
from typing import Any, Optional

from nonebot.adapters import Message, MessageSegment

# OneBot 群成员角色中「有管理权限」的取值
ADMIN_ROLES = frozenset({"owner", "admin"})

# OneBot set_group_ban 的时长上限：30 天
BAN_MAX_SECONDS = 30 * 86400

# 时长文本：数字 + 可选单位（支持中英文），如 "30m" / "2小时" / "45" / "1.5h"
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z一-鿿]*)$")
_DURATION_UNITS = {
    "": 1,
    "s": 1, "sec": 1, "second": 1, "seconds": 1, "秒": 1,
    "m": 60, "min": 60, "minute": 60, "minutes": 60, "分": 60, "分钟": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "时": 3600, "小时": 3600,
    "d": 86400, "day": 86400, "days": 86400, "天": 86400, "日": 86400,
}


def format_duration(seconds: int) -> str:
    """把秒数格式化为中文时长，如 ``26423`` -> ``7小时20分23秒``。"""
    seconds = int(seconds)
    if seconds <= 0:
        return "0秒"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if secs:
        parts.append(f"{secs}秒")
    return "".join(parts)


def parse_duration(text: str) -> Optional[int]:
    """解析禁言时长文本，返回秒；非法输入返回 ``None``。

    ``"45"`` -> 45，``"30m"`` -> 1800，``"2小时"`` -> 7200，``"1.5h"`` -> 5400。
    结果会被裁剪到 OneBot 允许的 1 秒 ~ 30 天。
    """
    if not text:
        return None
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None
    number, unit = m.group(1), m.group(2).lower()
    if unit not in _DURATION_UNITS:
        return None
    seconds = int(round(float(number) * _DURATION_UNITS[unit]))
    if seconds < 1:
        return None
    return min(seconds, BAN_MAX_SECONDS)


def is_group_admin(event: Any) -> bool:
    """事件发送者是否为群主 / 群管。"""
    sender = getattr(event, "sender", None)
    role = getattr(sender, "role", None)
    return role in ADMIN_ROLES


def is_extra_admin(event: Any, extra_admins: set) -> bool:
    """事件发送者是否在配置的额外管理员名单里。"""
    try:
        return int(event.get_user_id()) in {int(x) for x in extra_admins}
    except (AttributeError, TypeError, ValueError):
        return False


def is_superuser(event: Any, superusers: set) -> bool:
    """事件发送者是否为机器人超级用户。"""
    try:
        return event.get_user_id() in {str(x) for x in superusers}
    except (AttributeError, TypeError):
        return False


def extract_target(event: Any, arg: Message, self_id: str) -> Optional[int]:
    """从命令消息中解析目标用户 QQ 号，优先级：回复 > @ > 纯文本 QQ 号。

    - ``/烟 @NEKO☆PAFF``            -> NEKO☆PAFF 的 QQ 号
    - 回复某条消息后 ``/烟``          -> 被回复消息的发送者
    - ``/禁言 123456 30m``           -> 123456

    @机器人 自身的 at 段会被跳过；@全体成员（qq=all）被忽略。
    """
    # 1. 回复的消息（event.reply 由 OneBot 适配器经 get_msg 填充）
    reply = getattr(event, "reply", None)
    if reply is not None:
        sender = getattr(reply, "sender", None)
        user_id = getattr(sender, "user_id", None)
        if user_id:
            try:
                return int(user_id)
            except (TypeError, ValueError):
                pass

    # 2. @ 提到的人
    try:
        self_int = int(self_id) if self_id else None
    except (TypeError, ValueError):
        self_int = None
    for seg in event.get_message():
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq is None:
                continue
            try:
                qq_int = int(qq)
            except (TypeError, ValueError):
                continue  # @全体成员 等非数字 at
            if self_int is not None and qq_int == self_int:
                continue  # 跳过 @机器人 自己
            return qq_int

    # 3. 参数里的纯 QQ 号（取第一段，兼容 "/禁言 123456 30m"）
    text = arg.extract_plain_text().strip()
    first = text.split()[0] if text.split() else ""
    if first.isdigit():
        return int(first)
    return None


def arg_text_without_target(arg: Message, target: Optional[int]) -> str:
    """去掉参数里已被识别为目标的纯文本 QQ 号，返回剩余文本（用于解析时长）。

    例：``/禁言 123456 30m`` 的目标是 123456，剩余文本为 ``"30m"``；
    ``/禁言 @他 30m`` 的 CommandArg 纯文本本来就是 ``"30m"``，原样返回。
    """
    text = arg.extract_plain_text().strip()
    if target is None or not text:
        return text
    parts = text.split(maxsplit=1)
    if parts[0].isdigit() and int(parts[0]) == target:
        return parts[1].strip() if len(parts) > 1 else ""
    return text



def random_duration(min_seconds: int, max_seconds: int) -> int:
    """在 [min_seconds, max_seconds] 内闭区间随机取一个整数秒。"""
    lo, hi = sorted((int(min_seconds), int(max_seconds)))
    return random.randint(lo, hi)

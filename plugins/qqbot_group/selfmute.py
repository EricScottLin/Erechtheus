"""关键词触发的「禁言套餐 / 精致睡眠」。

- 精致睡眠：随机 6~12 小时禁言自己
- 禁言套餐：随机 1~60 分钟禁言自己
- 不指定用户时只对自己生效；只有群主/群管可以用 @ / 回复 / QQ 号指定别人
"""

import random

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)

from . import features
from .config import plugin_config
from .helpers import extract_target, format_duration
from .permissions import check_bot_admin, check_sender_admin, describe_error, finish_denial

SLEEP_KEYWORDS = ("精致睡眠", "睡个好觉", "精致睡眠套餐")
PACKAGE_KEYWORDS = ("禁言套餐", "禁言大礼包", "闭嘴套餐", "禁言我")
ALL_KEYWORDS = SLEEP_KEYWORDS + PACKAGE_KEYWORDS


async def _keyword_rule(event: GroupMessageEvent) -> bool:
    if not features.is_enabled(event.group_id, "selfmute"):
        return False
    text = event.get_plaintext()
    return any(keyword in text for keyword in ALL_KEYWORDS)


# 优先级 6：在普通命令（5）之后、复读（90）之前
selfmute = on_message(rule=_keyword_rule, priority=6, block=True)


def _resolve_target(event: GroupMessageEvent, bot: Bot) -> int | None:
    """@ / 回复 优先；其次允许消息里直接写 QQ 号。"""
    target = extract_target(event, Message(), bot.self_id)
    if target is not None:
        return target
    for token in event.get_plaintext().replace("，", " ").split():
        token = token.strip()
        if token.isdigit() and 5 <= len(token) <= 12:
            return int(token)
    return None


def _duration_seconds(kind: str) -> int:
    if kind == "sleep":
        lo = min(plugin_config.qqbot_group_sleep_min_hours,
                 plugin_config.qqbot_group_sleep_max_hours)
        hi = max(plugin_config.qqbot_group_sleep_min_hours,
                 plugin_config.qqbot_group_sleep_max_hours)
        return random.randint(lo, hi) * 3600

    lo = min(plugin_config.qqbot_group_package_min_minutes,
             plugin_config.qqbot_group_package_max_minutes)
    hi = max(plugin_config.qqbot_group_package_min_minutes,
             plugin_config.qqbot_group_package_max_minutes)
    return random.randint(lo, hi) * 60


@selfmute.handle()
async def handle_selfmute(bot: Bot, event: GroupMessageEvent) -> None:
    text = event.get_plaintext()
    kind = "sleep" if any(k in text for k in SLEEP_KEYWORDS) else "package"
    sender_id = int(event.get_user_id())
    target = _resolve_target(event, bot)

    # 指定别人：只有群主/群管可以
    if target is not None and target != sender_id:
        denial = check_sender_admin(bot, event)
        if denial:
            await selfmute.finish("只有群主/群管才能指定别人，普通成员只能给自己来一份。")

    # 机器人必须是管理员才能禁言
    denial = await check_bot_admin(bot, event)
    if denial:
        await finish_denial(selfmute, bot, event, denial)

    if target is None:
        target = sender_id

    duration = _duration_seconds(kind)
    try:
        await bot.call_api(
            "set_group_ban",
            group_id=event.group_id,
            user_id=target,
            duration=duration,
        )
    except ActionFailed as e:
        await selfmute.finish(f"禁言失败（{describe_error(e)}）——对方可能是群主或其他管理员。")
    except Exception as e:  # noqa: BLE001
        await selfmute.finish(f"禁言请求发送失败：{type(e).__name__}: {e}")

    name = "精致睡眠" if kind == "sleep" else "禁言套餐"
    emoji = "🌙" if kind == "sleep" else "🎁"
    if target == sender_id:
        await selfmute.finish(f"{emoji} {name}已生效：{format_duration(duration)} 后见～")
    await selfmute.finish(
        f"{emoji} 已给 {MessageSegment.at(target)} 安排{name}：{format_duration(duration)}"
    )

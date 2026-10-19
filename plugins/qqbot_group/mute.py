"""禁言相关命令：/烟（随机禁言）、/禁言、/解禁。"""

import random

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg

from .config import plugin_config
from .helpers import (
    arg_text_without_target,
    extract_target,
    format_duration,
    parse_duration,
    random_duration,
)
from .permissions import check_admin, describe_error, finish_denial

# 随机禁言的播报文案，抽一条
_SMOKE_LINES = [
    "🚬 给 {at} 递了根烟，接下来 {dur} 请安静",
    "🌫️ {at} 被烟雾蒙了嘴，{dur} 后放行",
    "🚭 {at} 领取了 {dur} 禁言套餐",
    "💨 {at} 抽了口烟，接下来 {dur} 只能心里吐槽",
]


# ---------------------------------------------------------------- /烟
smoke = on_command(
    "烟", aliases={"抽烟", "吸烟", "来一根"}, priority=5, block=True
)


@smoke.handle()
async def handle_smoke(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    """随机禁言：对目标执行一段 8 小时以内的随机时长禁言。"""
    denial = await check_admin(bot, event)
    if denial:
        await finish_denial(smoke, bot, event, denial)

    target = extract_target(event, arg, bot.self_id)
    if target is None:
        await smoke.finish("用法：/烟 @用户（也可以回复某条消息后发 /烟）")

    duration = random_duration(
        plugin_config.qqbot_group_smoke_min_seconds,
        plugin_config.qqbot_group_smoke_max_seconds,
    )

    try:
        await bot.call_api(
            "set_group_ban",
            group_id=event.group_id,
            user_id=target,
            duration=duration,
        )
    except ActionFailed as e:
        await smoke.finish(
            f"禁言失败（{describe_error(e)}）——对方可能是群主或其他管理员，动不了。"
        )
    except Exception as e:  # noqa: BLE001
        await smoke.finish(f"禁言请求发送失败：{type(e).__name__}: {e}")

    # 模板里的 {at} 要换成真正的 at 消息段，不能走 str.format（会退化成纯文本）
    head, _, tail = random.choice(_SMOKE_LINES).partition("{at}")
    await smoke.finish(
        MessageSegment.text(head)
        + MessageSegment.at(target)
        + MessageSegment.text(tail.format(dur=format_duration(duration)))
        + f"（随机 {duration} 秒）"
    )


# ---------------------------------------------------------------- /禁言
ban = on_command("禁言", aliases={"ban"}, priority=5, block=True)


@ban.handle()
async def handle_ban(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    """指定时长禁言，默认 10 分钟，最长 30 天。"""
    denial = await check_admin(bot, event)
    if denial:
        await finish_denial(ban, bot, event, denial)

    target = extract_target(event, arg, bot.self_id)
    if target is None:
        await ban.finish(
            "用法：/禁言 @用户 [时长]，如 /禁言 @他 30m；默认 "
            f"{format_duration(plugin_config.qqbot_group_ban_default_seconds)}，最长 30 天"
        )

    text = arg_text_without_target(arg, target)
    if text:
        duration = parse_duration(text)
        if duration is None:
            await ban.finish(
                f"看不懂时长「{text}」，支持 45 / 30m / 2小时 / 1d 这样的写法。"
            )
    else:
        duration = plugin_config.qqbot_group_ban_default_seconds

    try:
        await bot.call_api(
            "set_group_ban",
            group_id=event.group_id,
            user_id=target,
            duration=duration,
        )
    except ActionFailed as e:
        await ban.finish(f"禁言失败（{describe_error(e)}）——对方可能是群主或其他管理员。")
    except Exception as e:  # noqa: BLE001
        await ban.finish(f"禁言请求发送失败：{type(e).__name__}: {e}")

    await ban.finish(f"🔇 已禁言 {MessageSegment.at(target)} {format_duration(duration)}")


# ---------------------------------------------------------------- /解禁
unban = on_command("解禁", aliases={"unban", "解除禁言"}, priority=5, block=True)


@unban.handle()
async def handle_unban(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    """解除禁言。"""
    denial = await check_admin(bot, event)
    if denial:
        await finish_denial(unban, bot, event, denial)

    target = extract_target(event, arg, bot.self_id)
    if target is None:
        await unban.finish("用法：/解禁 @用户（也可以回复某条消息后发 /解禁）")

    try:
        await bot.call_api(
            "set_group_ban",
            group_id=event.group_id,
            user_id=target,
            duration=0,
        )
    except ActionFailed as e:
        await unban.finish(f"解禁失败（{describe_error(e)}）。")
    except Exception as e:  # noqa: BLE001
        await unban.finish(f"解禁请求发送失败：{type(e).__name__}: {e}")

    await unban.finish(f"🔊 已解除 {MessageSegment.at(target)} 的禁言")

"""全员禁言开关：/全体禁言、/解除全体。"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, GroupMessageEvent

from .permissions import check_admin, describe_error, finish_denial

mute_all = on_command(
    "全体禁言", aliases={"全员禁言", "muteall"}, priority=5, block=True
)


@mute_all.handle()
async def handle_mute_all(bot: Bot, event: GroupMessageEvent) -> None:
    """开启全员禁言。"""
    denial = await check_admin(bot, event)
    if denial:
        await finish_denial(mute_all, bot, event, denial)

    try:
        await bot.call_api(
            "set_group_whole_ban", group_id=event.group_id, enable=True
        )
    except ActionFailed as e:
        await mute_all.finish(f"开启全体禁言失败（{describe_error(e)}）。")
    except Exception as e:  # noqa: BLE001
        await mute_all.finish(f"开启全体禁言失败：{type(e).__name__}: {e}")

    await mute_all.finish("🔇 已开启全体禁言，群管除外")


unmute_all = on_command(
    "解除全体",
    aliases={"取消全体禁言", "解除全员禁言", "取消全员禁言", "unmuteall"},
    priority=5,
    block=True,
)


@unmute_all.handle()
async def handle_unmute_all(bot: Bot, event: GroupMessageEvent) -> None:
    """关闭全员禁言。"""
    denial = await check_admin(bot, event)
    if denial:
        await finish_denial(unmute_all, bot, event, denial)

    try:
        await bot.call_api(
            "set_group_whole_ban", group_id=event.group_id, enable=False
        )
    except ActionFailed as e:
        await unmute_all.finish(f"关闭全体禁言失败（{describe_error(e)}）。")
    except Exception as e:  # noqa: BLE001
        await unmute_all.finish(f"关闭全体禁言失败：{type(e).__name__}: {e}")

    await unmute_all.finish("🔊 已关闭全体禁言")

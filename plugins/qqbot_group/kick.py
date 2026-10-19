"""踢人命令：/踢。"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

from .helpers import extract_target
from .permissions import check_admin, describe_error, finish_denial

kick = on_command("踢", aliases={"kick", "移出群聊"}, priority=5, block=True)


@kick.handle()
async def handle_kick(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    """移出群聊。"""
    denial = await check_admin(bot, event)
    if denial:
        await finish_denial(kick, bot, event, denial)

    target = extract_target(event, arg, bot.self_id)
    if target is None:
        await kick.finish("用法：/踢 @用户（也可以回复某条消息后发 /踢）")

    try:
        await bot.call_api(
            "set_group_kick",
            group_id=event.group_id,
            user_id=target,
            reject_add_request=False,
        )
    except ActionFailed as e:
        await kick.finish(f"踢人失败（{describe_error(e)}）——对方可能是群主或其他管理员。")
    except Exception as e:  # noqa: BLE001
        await kick.finish(f"踢人请求发送失败：{type(e).__name__}: {e}")

    await kick.finish(f"👢 已将 {MessageSegment.at(target)} 移出本群")

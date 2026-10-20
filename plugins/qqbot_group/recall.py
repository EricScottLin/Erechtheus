"""撤回命令：/撤（撤回机器人自己上一条消息）。"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, GroupMessageEvent
from .rules import explicit_prefix_or_at

from .permissions import _last_sent, check_sender_admin, describe_error, finish_denial


recall = on_command(
    "撤",
    aliases={"撤回"},
    rule=explicit_prefix_or_at(),
    priority=5,
    block=True,
)


@recall.handle()
async def handle_recall(bot: Bot, event: GroupMessageEvent) -> None:
    """撤回机器人自己在群里发送的上一条消息（仅管理员可用）。"""
    denial = check_sender_admin(bot, event)
    if denial:
        await finish_denial(recall, bot, event, denial)

    message_id = _last_sent.get(event.group_id)
    if message_id is None:
        await recall.finish("我还没在本群发过消息，没有可撤回的（只记录本次启动后的消息）。")

    try:
        await bot.call_api("delete_msg", message_id=message_id)
    except ActionFailed as e:
        await recall.finish(
            f"撤回失败（{describe_error(e)}）——消息可能已超过 2 分钟或已被撤回。"
        )
    except Exception as e:  # noqa: BLE001
        await recall.finish(f"撤回请求发送失败：{type(e).__name__}: {e}")

    await recall.finish("🧹 已撤回我上一条消息")

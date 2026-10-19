"""撤回命令：/撤（撤回机器人自己上一条消息）。"""

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, GroupMessageEvent
from nonebot.rule import Rule

from .permissions import _last_sent, check_sender_admin, describe_error, finish_denial


def _recall_trigger_rule() -> Rule:
    """只允许「显式命令前缀」或「@机器人」触发 /撤。

    本项目 COMMAND_START 含 ""，裸的「撤」「撤回」在群聊里太容易误触发，
    因此排除裸词；但 ``@机器人 撤`` 会被 OneBot 适配器剥掉 @ 段后触发
    （``event.to_me`` 为 True），这是允许的。
    """
    starts = tuple(s for s in get_driver().config.command_start if s)

    async def _checker(event) -> bool:
        if getattr(event, "to_me", False):
            return True
        if not starts:
            return True
        text = event.get_plaintext().lstrip()
        return any(text.startswith(s) for s in starts)

    return Rule(_checker)


recall = on_command(
    "撤",
    aliases={"撤回"},
    rule=_recall_trigger_rule(),
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

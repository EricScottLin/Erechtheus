"""电猪：/电 @某人，固定发一张电猪表情包。"""

from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

from .features import is_enabled
from .roast import _explicit_prefix_or_at, _extract_target

ASSET = Path(__file__).parent / "assets" / "dianzhu.jpg"

dian_cmd = on_command(
    "电",
    aliases={"电猪", "电他"},
    rule=_explicit_prefix_or_at(),
    priority=6,
    block=True,
)


@dian_cmd.handle()
async def handle_dian(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    if not is_enabled(event.group_id, "dian"):
        await dian_cmd.finish("本群的「电」功能没开，管理员可以让我开。")

    target = _extract_target(event, bot)
    if target is None:
        target = int(event.get_user_id())

    head = MessageSegment.at(target) + MessageSegment.text(" ⚡ ")
    if ASSET.is_file():
        await dian_cmd.finish(head + MessageSegment.image(ASSET.read_bytes()))
    await dian_cmd.finish(head + MessageSegment.text("电一下！"))

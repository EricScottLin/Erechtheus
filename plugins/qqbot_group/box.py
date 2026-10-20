"""开盒命令：/盒 返回指定用户的 QQ 号。"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

from . import assets, features
from .helpers import extract_target
from .rules import explicit_prefix_or_at

box_cmd = on_command(
    "盒",
    aliases={"开盒", "查QQ", "查qq"},
    rule=explicit_prefix_or_at(),
    priority=5,
    block=True,
)


@box_cmd.handle()
async def handle_box(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    """返回被 @ / 回复 / 输入的 QQ 号。"""
    if not features.is_enabled(event.group_id, "box"):
        await box_cmd.finish("本群的「盒」功能被管理员关闭了。")

    target = extract_target(event, arg, bot.self_id)
    if target is None:
        await box_cmd.finish("用法：/盒 @用户（也可以回复某条消息后发 /盒）")

    msg = (
        MessageSegment.text("📦 开盒成功：")
        + MessageSegment.at(target)
        + MessageSegment.text(f" 的 QQ 是 {target}")
    )

    collected = await assets.collect_assets(str(target))
    if collected:
        msg += MessageSegment.text("\n──── 资产 ────")
        for source, lines in collected:
            msg += MessageSegment.text(f"\n【{source}】\n" + "\n".join(lines))

    await box_cmd.finish(msg)

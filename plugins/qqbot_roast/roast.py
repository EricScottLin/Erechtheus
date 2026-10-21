"""吐槽命令 /骂：安全版嘴臭，对事不对人。

- 不带脏字、不做人身攻击，只是一些整活吐槽
- @ / 回复 / QQ 号指定对象；不指定就吐槽发言者自己
- 默认关闭，由 qqbot_group 的按群功能开关控制
- 可用 data/qqbot_group/roasts.json 追加文案
"""

import json
import random

import nonebot
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .features import is_enabled

LINES = [
    "你今天的发言水平，建议先充个电再来。",
    "别人是深藏不露，你是深藏不露馅。",
    "你这操作，属于是把简单模式硬玩成地狱难度。",
    "建议你照照镜子，看看自己有多久没被夸过了。",
    "你这脑回路，导航看了都得重新规划路线。",
    "你说话挺有道理的，就是道理不太讲道理。",
    "别人是灵光一闪，你是灵光一闪而过，啥也没留下。",
    "你这波操作，属于是给对手送温暖。",
    "建议你把这股自信匀一点给需要的人。",
    "你也不是没有优点，比如你挺有自知之明的——虽然不多。",
    "你的思路很清晰，清晰到一眼就能看出是错的。",
    "别人是来聊天的，你是来给聊天增加难度的。",
    "你这嘴，不去参加辩论赛可惜了，去了对手也可惜了。",
    "建议你把「我觉得」三个字设为敏感词。",
    "你这一开口，我就知道今天群里的 KPI 有着落了。",
    "你的存在证明了：自信确实不需要门槛。",
    "别人发言是抛砖引玉，你发言是抛砖砸脚。",
    "你这逻辑，属于是左脚踩右脚想上天。",
    "建议你少说两句，给其他群友一点发挥空间。",
    "你也不是一无是处，至少你还能当个反面教材。",
]


def _extra_lines() -> list[str]:
    plugin = nonebot.get_plugin("qqbot_group")
    data_dir = None
    if plugin is not None:
        data_dir = getattr(getattr(plugin.module, "features", None), "DATA_DIR", None)
    if data_dir is None:
        return []
    path = data_dir / "roasts.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if isinstance(raw, dict):
        raw = raw.get("lines", [])
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def all_lines() -> list[str]:
    return LINES + _extra_lines()


def _explicit_prefix_or_at() -> Rule:
    starts = tuple(s for s in nonebot.get_driver().config.command_start if s)

    async def _checker(event) -> bool:
        if getattr(event, "to_me", False):
            return True
        if not starts:
            return True
        text = event.get_plaintext().lstrip()
        return any(text.startswith(s) for s in starts)

    return Rule(_checker)


def _extract_target(event: GroupMessageEvent, bot: Bot) -> int | None:
    for seg in event.get_message():
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        try:
            target = int(qq)
        except (TypeError, ValueError):
            continue
        if str(target) == str(bot.self_id):
            continue
        return target
    reply = getattr(event, "reply", None)
    sender = getattr(reply, "sender", None) if reply is not None else None
    uid = getattr(sender, "user_id", None) if sender is not None else None
    if uid:
        try:
            return int(uid)
        except (TypeError, ValueError):
            pass
    return None


roast_cmd = on_command(
    "骂",
    aliases={"吐槽", "锐评"},
    rule=_explicit_prefix_or_at(),
    priority=6,
    block=True,
)


@roast_cmd.handle()
async def handle_roast(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    if not is_enabled(event.group_id, "roast"):
        await roast_cmd.finish("本群的「骂」功能没开，管理员可以让我开。")

    target = _extract_target(event, bot)
    if target is None:
        target = int(event.get_user_id())

    line = random.choice(all_lines())
    await roast_cmd.finish(
        MessageSegment.text(f"🗣️ {line}\n—— 对 ")
        + MessageSegment.at(target)
        + MessageSegment.text(" 说的")
    )

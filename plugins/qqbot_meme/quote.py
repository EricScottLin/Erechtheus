"""金句库命令：/金句、/金句添加、/金句同步、@某人 说过什么。"""

import random

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from . import essence, feature_gate, library
from .config import plugin_config
from .permissions import is_admin

quote_cmd = on_command("金句", priority=5, block=True)
quote_add = on_command("金句添加", aliases={"添加金句"}, priority=5, block=True)
quote_sync = on_command("金句同步", aliases={"同步金句"}, priority=5, block=True)


@quote_cmd.handle()
async def handle_quote(event: MessageEvent) -> None:
    """随机一条金句。"""
    if not plugin_config.qqbot_meme_quote_enabled:
        await quote_cmd.finish("金句库还没启用。")
    if not feature_gate.is_enabled(getattr(event, "group_id", None), "quotes"):
        await quote_cmd.finish("本群的金句功能被管理员关闭了。")
    group_id = getattr(event, "group_id", None)
    entry = library.random_quote_entry(group_id)
    if entry is None:
        await quote_cmd.finish("金句库还是空的，之后会开放收录～")
    line = f"📜 {entry['text']}"
    if entry.get("author"):
        line += f"\n—— {entry['author']}"
    await quote_cmd.finish(line)


@quote_add.handle()
async def handle_quote_add(
    bot: Bot, event: MessageEvent, arg: Message = CommandArg()
) -> None:
    """收录一条金句。"""
    if not is_admin(bot, event):
        await quote_add.finish("只有群主/群管或机器人超级用户能收录金句。")

    text = arg.extract_plain_text().strip()
    if not text:
        await quote_add.finish("用法：/金句添加 <一句话>")

    total = await library.append_quote(text, group_id=getattr(event, "group_id", None))
    await quote_add.finish(f"✅ 已收录，本群金句库现有 {total} 条")


@quote_sync.handle()
async def handle_quote_sync(bot: Bot, event: MessageEvent) -> None:
    """立即同步一次群精华（不用等定时任务）。"""
    if not is_admin(bot, event):
        await quote_sync.finish("只有群主/群管或机器人超级用户能触发精华同步。")
    try:
        groups, added = await essence.sync_once(bot)
    except Exception as e:  # noqa: BLE001
        await quote_sync.finish(f"同步失败：{type(e).__name__}: {e}")
    await quote_sync.finish(f"✅ 已检查 {groups} 个群的精华，新增 {added} 条金句")


# ---------------------------------------------------------------- @某人 说过什么
_ASK_WORDS = ("说过什么", "说过啥", "说了什么", "说过哪些", "有什么名言")


def _target_user(event: MessageEvent, self_id: str) -> int | None:
    """从 @ 或回复里取目标 QQ。"""
    for seg in event.get_message():
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        try:
            target = int(qq)
        except (TypeError, ValueError):
            continue
        if str(target) == str(self_id):
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


async def _ask_rule(event: MessageEvent) -> bool:
    if getattr(event, "group_id", None) is None:
        return False
    if not any(word in event.get_plaintext() for word in _ASK_WORDS):
        return False
    return _target_user(event, event.self_id) is not None


ask_cmd = on_message(rule=_ask_rule, priority=6, block=True)


@ask_cmd.handle()
async def handle_ask(bot: Bot, event: MessageEvent) -> None:
    """发出某人在本群说过的一句金句。"""
    target = _target_user(event, bot.self_id)
    if target is None:
        await ask_cmd.finish("用法：@某人 说过什么")

    group_id = getattr(event, "group_id", None)
    if not feature_gate.is_enabled(group_id, "quotes"):
        await ask_cmd.finish("本群的金句功能被管理员关闭了。")

    names: list[str] = []
    try:
        info = await bot.call_api(
            "get_group_member_info",
            group_id=event.group_id,
            user_id=target,
            no_cache=True,
        )
        if isinstance(info, dict):
            names = [str(info.get(k) or "").strip() for k in ("card", "nickname")]
    except Exception:  # noqa: BLE001 —— 取不到昵称就只用 QQ 匹配
        pass

    entries = library.quotes_by_user(group_id, str(target), names)
    if not entries:
        await ask_cmd.finish(
            f"没有找到 {MessageSegment.at(target)} 说过的金句～"
            "（只收录本群精华里的话，管理员发 /金句同步 可更新）"
        )

    entry = random.choice(entries)
    line = f"📜 {entry['text']}"
    if entry.get("author"):
        line += f"\n—— {entry['author']}"
    await ask_cmd.finish(line)

"""金句库命令（预留）：/金句、/金句添加。"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
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

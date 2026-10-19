"""戳一戳 / 拍一拍：对机器人随机回一张表情包。"""

import time

from nonebot import on_notice
from nonebot.adapters.onebot.v11 import Bot, MessageSegment, PokeNotifyEvent

from . import feature_gate, providers
from .config import plugin_config

poke = on_notice(priority=5, block=False)

# 会话 key（群号或用户号）-> 上次回图时间戳
_last_reply: dict[str, float] = {}
# 空库提示单独冷却，避免每次戳都刷屏（10 分钟）
_last_hint: dict[str, float] = {}
EMPTY_HINT_COOLDOWN = 600


@poke.handle()
async def handle_poke(bot: Bot, event: PokeNotifyEvent) -> None:
    """被戳一戳时随机抽一张表情包回敬。"""
    if str(event.target_id) != str(bot.self_id):
        return  # 戳的不是机器人
    if str(event.user_id) == str(bot.self_id):
        return  # 机器人自己戳自己
    if not plugin_config.qqbot_meme_poke_enabled:
        return
    if not feature_gate.is_enabled(event.group_id, "poke"):
        return

    key = str(event.group_id or event.user_id)
    now = time.time()
    if now - _last_reply.get(key, 0.0) < plugin_config.qqbot_meme_poke_cooldown:
        return
    _last_reply[key] = now

    ctx = providers.ProviderContext(
        group_id=event.group_id, user_id=event.get_user_id()
    )
    item = await providers.pick_any(ctx)
    if item is None:
        if now - _last_hint.get(key, 0.0) < EMPTY_HINT_COOLDOWN:
            return
        _last_hint[key] = now
        await poke.finish("戳我也没有表情包……管理员用 /表情添加 塞点图给我吧")

    if item.kind == "image":
        await poke.finish(MessageSegment.image(item.value))
    await poke.finish(str(item.value))

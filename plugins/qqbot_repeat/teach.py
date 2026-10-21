"""我说你说：教机器人「听到 X 就回 Y」。

教学格式（触发词和回复都支持带不带空格）::

    我说 冰冰冰 你说              # 同时引用一张表情包/图片
    我说 你好 你说 你好呀          # 直接写文字回复
    我说 图图 你说 + 同一条消息里的图片
    我说 引用 你说                # 引用一条文字消息，回复那段文字

触发：群里有人发「冰冰冰」时，机器人回教学时指定的图片/文字。
规则按群隔离，默认只有群主/群管/超管能教和删。
"""

import re
import time

import httpx
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from . import features, permissions, store
from .config import plugin_config

# 「我说 <触发词> 你说 [回复]」；触发词非贪婪，遇到第一个「你说」就停
TEACH_RE = re.compile(r"^我说\s*(?P<trigger>.+?)\s*你说\s*(?P<reply>.*)$", re.S)
IMAGE_TYPES = ("image", "mface")

# (group_id, trigger) -> 冷却截止时间戳
_trigger_cooldown: dict[tuple[int, str], float] = {}


# ---------------------------------------------------------------- 解析
def _plain_text(message: Message) -> str:
    """只取文本段（忽略 @ / 回复 / 图片）。"""
    return "".join(
        str(seg.data.get("text") or "") for seg in message if seg.type == "text"
    )


def parse_teach(text: str) -> tuple[str, str] | None:
    """解析教学命令，返回 ``(触发词, 行内回复)``；不是教学命令返回 None。"""
    match = TEACH_RE.match((text or "").strip())
    if not match:
        return None
    return match.group("trigger").strip(), match.group("reply").strip()


def _valid_trigger(trigger: str) -> str | None:
    """校验触发词，返回错误文案；合法返回 None。"""
    cfg = plugin_config
    if not trigger:
        return "触发词不能为空。"
    if trigger.startswith("/"):
        return "触发词不能以 / 开头（那会被当成命令）。"
    if "我说" in trigger or "你说" in trigger:
        return "触发词里不能包含「我说」「你说」，换个词吧。"
    if "\n" in trigger or "\r" in trigger:
        return "触发词不能换行。"
    if len(trigger) > cfg.qqbot_repeat_teach_max_trigger_len:
        return f"触发词太长了（最多 {cfg.qqbot_repeat_teach_max_trigger_len} 个字）。"
    return None


def _image_url_from_message(message: Message | None) -> str:
    if message is None:
        return ""
    for seg in message:
        if seg.type not in IMAGE_TYPES:
            continue
        data = seg.data or {}
        for key in ("url", "file"):
            url = str(data.get(key) or "")
            if url.startswith("http"):
                return url
    return ""


async def _reply_message(bot: Bot, event: GroupMessageEvent) -> Message | None:
    """取被引用消息的正文：优先适配器填好的 ``event.reply``，否则 get_msg。"""
    reply = getattr(event, "reply", None)
    message = getattr(reply, "message", None) if reply is not None else None
    if message:
        return message

    for seg in event.get_message():
        if seg.type != "reply":
            continue
        raw_id = seg.data.get("id")
        try:
            data = await bot.call_api("get_msg", message_id=int(raw_id))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_repeat] get_msg({raw_id}) 失败：{e}")
            return None
        if not isinstance(data, dict):
            return None
        result = Message()
        for item in data.get("message") or []:
            if isinstance(item, dict) and item.get("type"):
                result += MessageSegment(item["type"], item.get("data") or {})
        return result
    return None


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_repeat] 下载图片失败：{e}")
        return None
    if resp.status_code != 200:
        logger.warning(f"[qqbot_repeat] 下载图片 HTTP {resp.status_code}")
        return None
    return resp.content


# ---------------------------------------------------------------- 教学
def _teach_rule(event: GroupMessageEvent) -> bool:
    if not plugin_config.qqbot_repeat_teach_enabled:
        return False
    if not features.is_enabled(event.group_id, "teach"):
        return False
    return parse_teach(_plain_text(event.get_message())) is not None


teach_cmd = on_message(priority=10, block=True, rule=_teach_rule)


async def _save_image_rule(
    event: GroupMessageEvent, trigger: str, data: bytes
) -> None:
    cfg = plugin_config
    max_bytes = cfg.qqbot_repeat_teach_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        await teach_cmd.finish(
            f"图片太大了（上限 {cfg.qqbot_repeat_teach_max_mb}MB）。"
        )
    try:
        name = store.save_image(data)
    except ValueError:
        await teach_cmd.finish("只支持 png / jpg / gif / webp 格式哦。")

    try:
        replaced = await store.upsert_rule(
            event.group_id,
            trigger,
            "image",
            name,
            event.get_user_id(),
            cfg.qqbot_repeat_teach_max_rules,
        )
    except ValueError:
        await teach_cmd.finish(
            f"本群教学规则已达上限 {cfg.qqbot_repeat_teach_max_rules} 条，"
            "先用 /我说删除 <触发词> 清理一下。"
        )

    total = store.count_rules(event.group_id)
    verb = "已更新" if replaced else "记住了"
    await teach_cmd.finish(
        MessageSegment.text(
            f"✅ {verb}：有人说「{trigger}」就发这张图（本群第 {total} 条）\n"
        )
        + MessageSegment.image(data)
    )


async def _save_text_rule(
    event: GroupMessageEvent, trigger: str, text: str
) -> None:
    cfg = plugin_config
    if len(text) > cfg.qqbot_repeat_teach_max_reply_len:
        await teach_cmd.finish(
            f"回复内容太长了（最多 {cfg.qqbot_repeat_teach_max_reply_len} 个字）。"
        )
    try:
        replaced = await store.upsert_rule(
            event.group_id,
            trigger,
            "text",
            text,
            event.get_user_id(),
            cfg.qqbot_repeat_teach_max_rules,
        )
    except ValueError:
        await teach_cmd.finish(
            f"本群教学规则已达上限 {cfg.qqbot_repeat_teach_max_rules} 条，"
            "先用 /我说删除 <触发词> 清理一下。"
        )

    total = store.count_rules(event.group_id)
    verb = "已更新" if replaced else "记住了"
    await teach_cmd.finish(
        f"✅ {verb}：有人说「{trigger}」就回「{text}」（本群第 {total} 条）"
    )


@teach_cmd.handle()
async def handle_teach(bot: Bot, event: GroupMessageEvent) -> None:
    parsed = parse_teach(_plain_text(event.get_message()))
    if parsed is None:
        return
    trigger, inline_reply = parsed

    error = _valid_trigger(trigger)
    if error:
        await teach_cmd.finish(error)

    if not permissions.can_teach(bot, event):
        await teach_cmd.finish("只有群主、群管或机器人超级用户能教「我说你说」哦。")

    cfg = plugin_config
    reply_message = await _reply_message(bot, event)
    quoted_image_url = _image_url_from_message(reply_message)
    quoted_text = _plain_text(reply_message).strip() if reply_message else ""
    same_image_url = _image_url_from_message(event.get_message())

    # 优先级：行内文字 > 引用图片 > 引用文字 > 同条消息里的图片
    if inline_reply:
        await _save_text_rule(event, trigger, inline_reply)
    if quoted_image_url:
        data = await _download(quoted_image_url)
        if data is None:
            await teach_cmd.finish("图片下载失败（链接可能过期），重新引用一张再教一次。")
        await _save_image_rule(event, trigger, data)
    if quoted_text:
        await _save_text_rule(event, trigger, quoted_text)
    if same_image_url:
        data = await _download(same_image_url)
        if data is None:
            await teach_cmd.finish("图片下载失败（链接可能过期），重新发一张再教一次。")
        await _save_image_rule(event, trigger, data)

    await teach_cmd.finish(
        "没看到要回复的内容。用法：\n"
        "我说 <触发词> 你说（同时引用一张表情包）\n"
        "我说 <触发词> 你说 <文字>\n"
        "例：我说 冰冰冰 你说 + 引用图片"
    )


# ---------------------------------------------------------------- 触发
def _trigger_rule(event: GroupMessageEvent) -> bool:
    if not plugin_config.qqbot_repeat_teach_enabled:
        return False
    if not features.is_enabled(event.group_id, "teach"):
        return False
    text = _plain_text(event.get_message()).strip()
    if not text or text.startswith("/"):
        return False
    return (
        store.find_rule(
            event.group_id, text, plugin_config.qqbot_repeat_teach_match
        )
        is not None
    )


trigger_cmd = on_message(priority=11, block=True, rule=_trigger_rule)


@trigger_cmd.handle()
async def handle_trigger(bot: Bot, event: GroupMessageEvent) -> None:
    text = _plain_text(event.get_message()).strip()
    found = store.find_rule(
        event.group_id, text, plugin_config.qqbot_repeat_teach_match
    )
    if found is None:
        return
    trigger, rule = found
    if event.get_user_id() == bot.self_id:
        return

    cfg = plugin_config
    now = time.time()
    key = (event.group_id, trigger)
    if now < _trigger_cooldown.get(key, 0.0):
        # 冷却中：静默拦截，避免继续走到复读
        await trigger_cmd.finish()
    _trigger_cooldown[key] = now + cfg.qqbot_repeat_teach_cooldown

    # 顺手清理过期冷却，防止长期运行无限增长
    if len(_trigger_cooldown) > 2000:
        for item, until in list(_trigger_cooldown.items()):
            if until <= now:
                _trigger_cooldown.pop(item, None)

    if rule.get("kind") == "image":
        path = store.image_path(str(rule.get("value") or ""))
        if path is None or not path.is_file():
            await store.delete_rule(event.group_id, trigger)
            await trigger_cmd.finish("这条教学的图片不见了，管理员重新教一次吧。")
        await trigger_cmd.finish(MessageSegment.image(path.read_bytes()))

    await trigger_cmd.finish(str(rule.get("value") or ""))


# ---------------------------------------------------------------- 列表 / 删除
list_cmd = on_command(
    "我说列表", aliases={"复读列表", "教学列表"}, priority=5, block=True
)


@list_cmd.handle()
async def handle_list(event: GroupMessageEvent) -> None:
    rules = store.list_rules(event.group_id)
    if not rules:
        await list_cmd.finish(
            "本群还没有「我说你说」教学。\n"
            "教一句：我说 冰冰冰 你说 + 引用一张表情包"
        )

    lines = [f"📚 本群我说你说（{len(rules)} 条）"]
    for index, (trigger, rule) in enumerate(rules[:30], 1):
        if rule.get("kind") == "image":
            value = "图片"
        else:
            text = str(rule.get("value") or "")
            value = text if len(text) <= 30 else text[:30] + "…"
        lines.append(f"{index}. 「{trigger}」→ {value}")
    if len(rules) > 30:
        lines.append(f"…另有 {len(rules) - 30} 条未显示")
    lines.append("删除：/我说删除 <触发词>")
    await list_cmd.finish("\n".join(lines))


del_cmd = on_command(
    "我说删除", aliases={"复读删除", "教学删除", "忘记教学"}, priority=5, block=True
)


@del_cmd.handle()
async def handle_delete(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
) -> None:
    if not permissions.can_teach(bot, event):
        await del_cmd.finish("只有群主、群管或机器人超级用户能删除教学。")

    trigger = arg.extract_plain_text().strip()
    if not trigger:
        await del_cmd.finish("用法：/我说删除 <触发词>")

    if await store.delete_rule(event.group_id, trigger):
        await del_cmd.finish(
            f"🗑️ 已删除「{trigger}」，本群还剩 {store.count_rules(event.group_id)} 条"
        )
    await del_cmd.finish(f"本群没有「{trigger}」这条教学。")

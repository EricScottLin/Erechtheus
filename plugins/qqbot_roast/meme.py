"""梗图：群聊整句命中触发词就随机发一张；私聊命令上传。

命令（仅超级用户，默认私聊使用）：
    /梗                     查看使用说明
    /梗 群                    列出你所在的群及 group_id
    /梗 列表                  查看本群/指定群的触发词
    /梗 添加 <触发词> [@群]    上传图片（附一张图或回复一张图）
    /梗 删除 <触发词> [@群]    删除该触发词所有图
    /梗 清空 <触发词> [@群]    同删除
"""

import random
import re
from pathlib import Path

import nonebot
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.params import CommandArg

from . import library
from .features import is_enabled


def _is_superuser(event: MessageEvent) -> bool:
    supers = {str(uid) for uid in (nonebot.get_driver().config.superusers or set())}
    return str(event.get_user_id()) in supers


def _images_of(event: MessageEvent) -> list[str]:
    """取消息里的图片 URL（含引用消息里的图）。"""
    urls: list[str] = []
    for seg in event.get_message():
        if seg.type == "image":
            url = seg.data.get("url") or seg.data.get("file")
            if url:
                urls.append(str(url))
    reply = getattr(event, "reply", None)
    if reply is not None:
        for seg in reply.message:
            if seg.type == "image":
                url = seg.data.get("url") or seg.data.get("file")
                if url:
                    urls.append(str(url))
    return urls


async def _fetch(bot: Bot, url: str) -> bytes | None:
    try:
        if url.startswith("base64://"):
            import base64

            return base64.b64decode(url[len("base64://") :])
        if url.startswith("http://") or url.startswith("https://"):
            import httpx

            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
        # 其他形式（file:// 或裸文件名）交给 OneBot 取
        result = await bot.call_api("get_image", file=url)
        if isinstance(result, dict):
            file_path = result.get("file")
            if file_path and Path(file_path).is_file():
                return Path(file_path).read_bytes()
    except Exception:
        return None
    return None


def _parse(msg: Message) -> tuple[str, str, list[int]]:
    """拆出 ``(子命令, 触发词, 目标群列表)``。"""
    text = msg.extract_plain_text().strip()
    groups = [int(seg.data["qq"]) for seg in msg if seg.type == "at" and str(seg.data.get("qq", "")).isdigit()]
    parts = text.split(maxsplit=1)
    if not parts:
        return "", "", groups
    sub = parts[0]
    trigger = parts[1].strip() if len(parts) > 1 else ""
    return sub, trigger, groups


meme_cmd = on_command("梗", aliases={"梗图"}, priority=4, block=True)

HELP = """📚 梗图库（仅超级用户可上传）
/梗                    显示这条说明
/梗 群                  列出你所在的群和 group_id
/梗 列表 [@群]           查看某个群的触发词（默认本群）
/梗 添加 <触发词> [@群]   附一张图或回复一张图上传
/梗 删除 <触发词> [@群]   删除该触发词的所有图
群里整句说出触发词，就会随机发一张对应的图。"""


def _pick_group(event: MessageEvent, groups: list[int]) -> int | None:
    if groups:
        return groups[0]
    return getattr(event, "group_id", None)


@meme_cmd.handle()
async def handle_meme(bot: Bot, event: MessageEvent, arg: Message = CommandArg()) -> None:
    sub, trigger, groups = _parse(arg)

    if not _is_superuser(event):
        await meme_cmd.finish("梗图上传只开放给机器人超级用户，其他人请找管理员。")

    if sub in ("", "帮助", "help"):
        await meme_cmd.finish(HELP)

    if sub in ("群", "群列表", "群聊"):
        try:
            chat_list = await bot.call_api("get_group_list")
        except Exception as e:  # noqa: BLE001
            await meme_cmd.finish(f"获取群列表失败：{type(e).__name__}: {e}")
        lines = [f"{g.get('group_name', '?')}  group_id={g.get('group_id')}" for g in chat_list or []]
        await meme_cmd.finish("📋 我所在的群：\n" + ("\n".join(lines) if lines else "（没有）"))

    group_id = _pick_group(event, groups)
    if group_id is None:
        await meme_cmd.finish("请指定群：/梗 添加 <触发词> @某个群（先用 /梗 群 查 group_id）")

    if sub in ("列表", "list"):
        triggers = library.triggers_of(int(group_id))
        if not triggers:
            await meme_cmd.finish(f"群 {group_id} 还没有梗图，用 /梗 添加 <触发词> 上传。")
        lines = [f"· {k}（{len(v)} 张）" for k, v in sorted(triggers.items())]
        await meme_cmd.finish(f"📚 群 {group_id} 的触发词：\n" + "\n".join(lines))

    if sub in ("添加", "上传", "add"):
        if not trigger:
            await meme_cmd.finish("用法：/梗 添加 <触发词>（附一张图或回复一张图）")
        urls = _images_of(event)
        if not urls:
            await meme_cmd.finish("没找到图片：请带上图片，或回复一条带图片的消息再发这条命令。")
        added, dup = 0, 0
        for url in urls:
            data = await _fetch(bot, url)
            if not data:
                continue
            _, is_new = await library.add_meme(int(group_id), trigger, data)
            added += int(is_new)
            dup += int(not is_new)
        if not added and not dup:
            await meme_cmd.finish("图片下载失败，没有保存。")
        total = len(library.memes_for(int(group_id), trigger))
        await meme_cmd.finish(
            f"✅ 群 {group_id}「{trigger}」新增 {added} 张"
            + (f"，重复 {dup} 张已跳过" if dup else "")
            + f"，现在共 {total} 张。"
        )

    if sub in ("删除", "清空", "del"):
        if not trigger:
            await meme_cmd.finish("用法：/梗 删除 <触发词>")
        removed = await library.remove_meme(int(group_id), trigger)
        if not removed:
            await meme_cmd.finish(f"群 {group_id} 没有「{trigger}」这个触发词。")
        await meme_cmd.finish(f"🗑️ 已删除「{trigger}」的 {removed} 张图。")

    await meme_cmd.finish(HELP)


# ---------------------------------------------------------------- 群聊触发
async def _meme_rule(event: GroupMessageEvent) -> bool:
    if not is_enabled(event.group_id, "meme_reply"):
        return False
    text = event.get_plaintext().strip()
    if not text:
        return False
    return text in library.triggers_of(event.group_id)


# 优先级 8：在禁言套餐 / 问候之后
meme_reply = on_message(rule=_meme_rule, priority=8, block=True)


@meme_reply.handle()
async def handle_meme_reply(event: GroupMessageEvent) -> None:
    text = event.get_plaintext().strip()
    files = library.memes_for(event.group_id, text)
    if not files:
        return
    name = random.choice(files)
    path = library.image_path(event.group_id, name)
    if not path.is_file():
        return
    await meme_reply.finish(MessageSegment.image(path.read_bytes()))

"""表情包命令：/表情、/表情列表、/表情添加、/表情删除。"""

import time

import httpx
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from . import feature_gate, library, providers

try:
    from . import sheet
except Exception:  # noqa: BLE001 —— 没装 Pillow 时退化为逐张预览
    sheet = None  # type: ignore[assignment]
from .config import plugin_config
from .permissions import is_admin

random_cmd = on_command("表情", aliases={"发表情"}, priority=5, block=True)
list_cmd = on_command("表情列表", aliases={"列表表情"}, priority=5, block=True)
add_cmd = on_command("表情添加", aliases={"添加表情"}, priority=5, block=True)
del_cmd = on_command("表情删除", aliases={"删除表情"}, priority=5, block=True)
force_add_cmd = on_command(
    "表情强制添加", aliases={"强制添加表情"}, priority=5, block=True
)

# 最近图片缓存：发图后 10 分钟内可直接 /表情添加，无需引用
IMAGE_CACHE_TTL = 600
_last_image: dict[str, tuple[str, float]] = {}

image_watcher = on_message(priority=100, block=False)


@image_watcher.handle()
async def _watch_image(event: MessageEvent) -> None:
    """记录用户最近发送的图片 URL，供 /表情添加 兜底。"""
    url = _image_url_from_message(event.get_message())
    if url:
        _last_image[event.get_user_id()] = (url, time.time())


def _recent_image(user_id: str) -> str:
    cached = _last_image.get(user_id)
    if cached and time.time() - cached[1] <= IMAGE_CACHE_TTL:
        return cached[0]
    return ""


def _image_url_from_message(message: Message) -> str:
    """取图片段的地址。

    OneBot v11 的图片段可能把地址放在 ``url`` 或 ``file`` 里，也可能是
    ``base64://`` 内联数据，三种都要认。
    """
    for seg in message:
        if seg.type not in ("image", "mface"):
            continue
        for key in ("url", "file"):
            value = str(seg.data.get(key) or "")
            if value.startswith(("http://", "https://", "base64://", "file://")):
                return value
    return ""


async def _reply_image_url(bot: Bot, event: MessageEvent) -> str:
    """从「回复的消息」里取图片 URL。"""
    reply = getattr(event, "reply", None)
    if reply is not None:
        url = _image_url_from_message(reply.message)
        if url:
            return url
    # 兜底：reply 段里的 message_id -> get_msg
    for seg in event.get_message():
        if seg.type != "reply":
            continue
        raw_id = seg.data.get("id")
        try:
            msg = await bot.call_api("get_msg", message_id=int(raw_id))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] get_msg({raw_id}) 失败: {e}")
            return ""
        if isinstance(msg, dict):
            for item in msg.get("message") or []:
                if isinstance(item, dict) and item.get("type") in ("image", "mface"):
                    url = str((item.get("data") or {}).get("url") or "")
                    if url.startswith("http"):
                        return url
    return ""


async def _download(url: str) -> bytes | None:
    if url.startswith("base64://"):
        import base64

        try:
            return base64.b64decode(url[len("base64://") :])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 解码 base64 图片失败: {e}")
            return None
    if url.startswith("file://"):
        try:
            return Path(url[len("file://") :]).read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 读取本地图片失败: {e}")
            return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_meme] 下载图片失败: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(f"[qqbot_meme] 下载图片 HTTP {resp.status_code}")
        return None
    return resp.content


# ---------------------------------------------------------------- /表情
@random_cmd.handle()
async def handle_random(event: MessageEvent) -> None:
    """从所有图片来源里随机来一张。"""
    ctx = providers.ProviderContext(
        group_id=getattr(event, "group_id", None), user_id=event.get_user_id()
    )
    item = await providers.pick_image(ctx)
    if item is None:
        await random_cmd.finish("表情库还是空的，管理员可以用 /表情添加 投稿（回复一张图）")
    await random_cmd.finish(MessageSegment.image(item.value))


# ---------------------------------------------------------------- /表情列表
@list_cmd.handle()
async def handle_list(event: MessageEvent) -> None:
    """查看表情库总览（各来源数量）与本地文件列表。"""
    sources = await providers.describe_sources()
    files = library.list_stickers()

    lines = ["📚 表情库总览"]
    for name, kind, count in sources:
        label = "图片" if kind == "image" else "文本"
        shown = "未知" if count is None else f"{count} 个"
        lines.append(f"· {name}（{label}）：{shown}")
    lines.append(
        f"· 抽取权重：图片 {plugin_config.qqbot_meme_poke_image_weight} : "
        f"金句 {plugin_config.qqbot_meme_poke_quote_weight}，"
        f"来源加权模式 {plugin_config.qqbot_meme_source_weighting}"
    )

    if not files:
        lines.append("本地表情库还是空的，管理员可用 /表情添加 投稿。")
        await list_cmd.finish("\n".join(lines))

    # 拼成一张带序号的总览大图，避免逐张刷屏
    png = b""
    if sheet is not None:
        try:
            png = sheet.render_grid(
                files, title="本地表情", subtitle=f"共 {len(files)} 张"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 生成表情总览失败：{e}")

    if png:
        lines.append("────────────")
        lines.append("下面一张图就是全部本地表情，序号对应 /表情删除 <序号>")
        await list_cmd.finish(
            Message(MessageSegment.text("\n".join(lines))) + MessageSegment.image(png)
        )

    # 拼图失败时退化成逐张发送
    lines.append("────────────")
    lines.append("本地表情（拼图失败，逐张预览）：")
    await list_cmd.send("\n".join(lines))
    for i, p in enumerate(files, 1):
        try:
            data = p.read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 读取表情 {p.name} 失败：{e}")
            await list_cmd.send(f"{i}. {p.name}（读取失败）")
            continue
        await list_cmd.send(
            Message(MessageSegment.text(f"{i}.")) + MessageSegment.image(data)
        )
    await list_cmd.finish("删除哪张就发：/表情删除 <序号>")


# ---------------------------------------------------------------- /表情添加
@add_cmd.handle()
async def handle_add(bot: Bot, event: MessageEvent) -> None:
    """回复一张图（或图文一起发）把表情包收进库。"""
    if not is_admin(bot, event):
        await add_cmd.finish("只有群主/群管或机器人超级用户能添加表情包。")

    url = (
        _image_url_from_message(event.get_message())
        or await _reply_image_url(bot, event)
        or _recent_image(event.get_user_id())
    )
    if not url:
        await add_cmd.finish(
            "没有找到图片，三种方式任选：\n"
            "① 回复一张表情包，再发 /表情添加\n"
            "② 先发一张图，10 分钟内发 /表情添加\n"
            "③ 图片和 /表情添加 一起发"
        )

    data = await _download(url)
    if data is None:
        await add_cmd.finish("图片下载失败（链接可能已过期），重新发一张再试试。")

    max_bytes = plugin_config.qqbot_meme_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        await add_cmd.finish(f"图片太大了（上限 {plugin_config.qqbot_meme_max_mb}MB）。")

    # 先查重：完全一样 / 只是被重压缩过的同一张图
    dup = library.find_duplicate(data)
    if dup is not None:
        kind, existing = dup
        total = len(library.list_stickers())
        if kind == "same":
            head = f"这张已经在库里了（共 {total} 张）"
        else:
            head = f"这张和库里已有的一张几乎一样（共 {total} 张）"
        msg = Message(MessageSegment.text(head + "\n库里那张是：\n"))
        try:
            msg += MessageSegment.image(existing.read_bytes())
        except Exception:  # noqa: BLE001
            pass
        msg += MessageSegment.text("\n确实还要再存一份，就发 /表情强制添加")
        await add_cmd.finish(msg)

    try:
        path, created = library.save_sticker(data)
    except ValueError:
        await add_cmd.finish("只支持 png / jpg / gif / webp 格式哦。")

    total = len(library.list_stickers())
    if created:
        await add_cmd.finish(
            MessageSegment.text(f"✅ 已添加，库内共 {total} 张\n")
            + MessageSegment.image(data)
        )
    await add_cmd.finish(f"这张已经在库里了（共 {total} 张）")


# ---------------------------------------------------------------- /表情强制添加
@force_add_cmd.handle()
async def handle_force_add(bot: Bot, event: MessageEvent) -> None:
    """跳过查重，硬塞一张（确认不是重复时用）。"""
    if not is_admin(bot, event):
        await force_add_cmd.finish("只有群主/群管或机器人超级用户能添加表情包。")

    url = (
        _image_url_from_message(event.get_message())
        or await _reply_image_url(bot, event)
        or _recent_image(event.get_user_id())
    )
    if not url:
        await force_add_cmd.finish("没找到图片：回复一张图再发 /表情强制添加。")

    data = await _download(url)
    if data is None:
        await force_add_cmd.finish("图片下载失败（链接可能已过期），重新发一张再试试。")

    max_bytes = plugin_config.qqbot_meme_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        await force_add_cmd.finish(f"图片太大了（上限 {plugin_config.qqbot_meme_max_mb}MB）。")

    try:
        path, created = library.save_sticker(data)
    except ValueError:
        await force_add_cmd.finish("只支持 png / jpg / gif / webp 格式哦。")

    total = len(library.list_stickers())
    if created:
        await force_add_cmd.finish(f"✅ 已强制添加，库内共 {total} 张")
    await force_add_cmd.finish(f"字节完全一致，库里已有同一文件（共 {total} 张）")


# ---------------------------------------------------------------- /表情删除
@del_cmd.handle()
async def handle_delete(
    bot: Bot, event: MessageEvent, arg: Message = CommandArg()
) -> None:
    """按序号删除表情包。"""
    if not is_admin(bot, event):
        await del_cmd.finish("只有群主/群管或机器人超级用户能删除表情包。")

    text = arg.extract_plain_text().strip()
    if not text.isdigit():
        files = library.list_stickers()
        tip = f"共 {len(files)} 张" if files else "本地表情库是空的"
        await del_cmd.finish(
            f"用法：/表情删除 <序号>（{tip}），先发 /表情列表 逐张看预览"
        )

    path = library.delete_sticker(int(text) - 1)
    if path is None:
        await del_cmd.finish("没有这个序号，先发 /表情列表 看看。")
    await del_cmd.finish(f"🗑️ 已删除 {path.name}，还剩 {len(library.list_stickers())} 张")

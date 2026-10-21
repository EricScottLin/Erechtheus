"""小猪宇宙（Pigverse）—— rollpig-plus 之上的扩展功能集。

Phase 1：/猪猪成就    图鉴里程碑 → 一次性 Kleos（封闭卡池，总量 11,150）
Phase 2：/小猪卡      今日小猪渲染成 DXPASS 风格卡片
Phase 3：/斗猪        群友斗猪（每日 3 次）
Phase 4：/牧场 /喂食  小猪养成 + Drachma sink
互动：  「小猪小猪」关键词 → 猪叫 + 随机表情包；/猪猪表情 查看/投稿/总览
附加：  /吃什么       随机推荐一道菜（下厨房菜谱图）

设计规范见 docs/PIGVERSE_SPEC.md；经济规则见 docs/CURRENCY_SPEC.md。
"""

import asyncio
import math
import random
import re
import time

from nonebot import on_command, on_message, on_regex
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from . import dishes, memes
from .achievements import ACHIEVEMENTS, TOTAL_KLEOS, check_and_claim, progress_view
from .sheet import render_meme_sheet
from .store import get_user

__plugin_meta__ = PluginMetadata(
    name="小猪宇宙",
    description="猪猪图鉴成就 + 猪猪表情包：收集里程碑解锁 Kleos，发「小猪小猪」触发猪叫与表情",
    usage="""\
猪猪成就    查看收集进度，自动补发已达成的里程碑奖励
猪猪表情    查看表情包数量并随机来一张
猪猪表情 上传   先发一张图，回复它发送本命令即可入库
（群聊里直接发「小猪小猪」会得到一声猪叫 + 随机表情包）""",
    type="application",
    supported_adapters={"~onebot.v11"},
)

BAR_LEN = 12

# ---------------------------------------------------------------- 小猪小猪
PIG_SOUNDS = (
    "哼哼～",
    "哼唧哼唧～",
    "噗噜噗噜～",
    "吭哧吭哧…",
    "咕噜咕噜噜～",
    "哼！（猪鼻子喷气）",
    "嗅嗅～",
    "嗷哼！",
    "哼嗤哼嗤",
    "噜噜噜～",
)

PIG_CD_SECONDS = 30
_pig_cd: dict[str, float] = {}

pig_regex = on_regex(r"小猪小猪", priority=10, block=False)


@pig_regex.handle()
async def handle_pig_pig(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    now = time.time()
    if now - _pig_cd.get(uid, 0.0) < PIG_CD_SECONDS:
        return
    _pig_cd[uid] = now

    sound = random.choice(PIG_SOUNDS)
    meme = await memes.random_meme()
    if meme is not None:
        await pig_regex.finish(
            MessageSegment.text(sound + "\n") + MessageSegment.image(meme.read_bytes())
        )
    await pig_regex.finish(sound)


# ---------------------------------------------------------------- /猪猪表情
memes_cmd = on_command(
    "猪猪表情", aliases={"小猪表情", "猪猪表情包"}, priority=5, block=True
)


def _image_url_from_event(event: MessageEvent) -> str:
    for seg in event.get_message():
        if seg.type in ("image", "mface"):
            url = str(seg.data.get("url") or "")
            if url.startswith("http"):
                return url
    return ""


async def _image_url_from_reply(
    bot: Bot, event: MessageEvent
) -> tuple[str, str, int | None]:
    """引用回复：图片在 reply 指向的那条消息里，需要 get_msg 取回。

    返回 (url, reason, message_id)：message_id 是图片所在消息的 id（用于成功时引用）。
    """
    for seg in event.get_message():
        if seg.type != "reply":
            continue
        raw_id = seg.data.get("id")
        if raw_id is None:
            continue
        try:
            msg = await bot.get_msg(message_id=int(raw_id))
        except Exception as e:
            logger.warning(f"[pigverse] get_msg({raw_id}) 失败: {e}")
            return "", "取不到你引用的那条消息（可能太旧），请重新发一张图再上传", None
        if not isinstance(msg, dict):
            return "", "引用消息格式异常，请重新发一张图再上传", None
        segments = [it for it in (msg.get("message") or []) if isinstance(it, dict)]
        for item in segments:
            if item.get("type") in ("image", "mface"):
                url = str((item.get("data") or {}).get("url") or "")
                if url.startswith("http"):
                    return url, "", int(raw_id)
        types = [it.get("type") for it in segments]
        logger.info(f"[pigverse] 引用消息 {raw_id} 无图片段，类型={types}")
        return (
            "",
            f"你引用的消息里没有图片（内容类型：{', '.join(types) or '空'}），请引用一张图片或表情包",
            None,
        )
    return "", "", None


# 最近图片缓存：发图后 10 分钟内可免引用上传（引用操作在部分客户端不生效的兜底）
IMAGE_CACHE_TTL = 600
# 群级兜底：图是别人发的、或换号发指令时，用本群最近一张图（更短的有效期）
GROUP_IMAGE_TTL = 180
_last_image: dict[str, tuple[str, float, int]] = {}
_last_group_image: dict[str, tuple[str, float, int]] = {}

image_watcher = on_message(priority=100, block=False)


@image_watcher.handle()
async def _watch_image(event: MessageEvent) -> None:
    url = _image_url_from_event(event)
    if not url:
        return
    uid = str(event.get_user_id())
    stamp = (url, time.time(), int(event.message_id))
    _last_image[uid] = stamp
    gid = getattr(event, "group_id", None)
    if gid is not None:
        _last_group_image[str(gid)] = stamp


@memes_cmd.handle()
async def handle_memes(bot: Bot, event: MessageEvent, arg: Message = CommandArg()) -> None:
    sub = arg.extract_plain_text().strip()

    if sub in ("上传", "投稿", "添加"):
        url = _image_url_from_event(event)
        reason = ""
        image_msg_id: int | None = int(event.message_id) if url else None
        if not url:
            url, reason, image_msg_id = await _image_url_from_reply(bot, event)
        if not url:
            # 免引用兜底：取该用户最近 10 分钟内发过的图
            cached = _last_image.get(str(event.get_user_id()))
            if cached and time.time() - cached[1] < IMAGE_CACHE_TTL:
                url, image_msg_id = cached[0], cached[2]
        if not url:
            # 群级兜底：图可能是别人发的（或换了号发指令），用本群最近一张图
            gid = str(getattr(event, "group_id", "") or "")
            group_cached = _last_group_image.get(gid) if gid else None
            if group_cached and time.time() - group_cached[1] < GROUP_IMAGE_TTL:
                url, image_msg_id = group_cached[0], group_cached[2]
                logger.info(f"[pigverse] 使用本群最近图片兜底: group={gid}")
        if not url:
            if reason:
                await memes_cmd.finish(reason)
            await memes_cmd.finish(
                "上传方式（任选其一）：\n"
                "① 发一张图，然后 10 分钟内发 /猪猪表情 上传\n"
                "② 引用回复一张图（新旧都行、别人发的也行），再发 /猪猪表情 上传"
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"[pigverse] 表情包下载失败 HTTP {resp.status_code}: {url[:80]}")
                await memes_cmd.finish(f"图片下载失败（HTTP {resp.status_code}），图片链接可能已过期，重新发一张再传试试")
        except Exception as e:
            logger.warning(f"[pigverse] 表情包下载异常: {e}")
            await memes_cmd.finish("图片下载失败，稍后再试")
        result = await memes.add_meme(
            resp.content, uploader=str(event.get_user_id())
        )
        quote = MessageSegment.reply(image_msg_id or event.message_id)
        if not result.get("ok"):
            if result.get("reason") == "duplicate":
                existing = result.get("existing") or {}
                text = quote + (
                    f"这张已经收录过了（第 {existing.get('id', '?')} 张），换一张吧"
                )
                path = memes.meme_path(existing) if existing else None
                if (
                    path is not None
                    and path.is_file()
                    and path.stat().st_size <= memes.MAX_SEND_BYTES
                ):
                    await memes_cmd.finish(text + MessageSegment.image(path.read_bytes()))
                await memes_cmd.finish(text)
            await memes_cmd.finish("只支持 jpg/png/gif/webp，且不超过 2MB 哦")
        item = result["item"]
        await memes_cmd.finish(
            quote + f"收到！入库成功，这是第 {item['id']} 张猪猪表情包 🐷"
        )

    if sub in ("列表", "全部", "总览", "所有") or sub.startswith("列表"):
        page = 1
        matched = re.search(r"(\d+)", sub)
        if matched:
            page = max(1, int(matched.group(1)))
        total = await memes.count()
        items_all = await memes.all_memes()
        if not items_all:
            await memes_cmd.finish("表情库还是空的，发张图来投稿吧")
        page_size = 36
        pages = max(1, math.ceil(len(items_all) / page_size))
        page = min(page, pages)
        chunk = items_all[(page - 1) * page_size : page * page_size]
        png = await asyncio.to_thread(
            render_meme_sheet, chunk, total=total, page=page, pages=pages
        )
        await memes_cmd.finish(MessageSegment.image(png))

    if sub.isdigit():
        item = await memes.get_meme(int(sub))
        if item is None:
            await memes_cmd.finish(f"没有第 {sub} 张，发 /猪猪表情 列表 看看有哪些")
        path = memes.meme_path(item)
        if not path.is_file():
            await memes_cmd.finish("这张的图片文件丢了…")
        await memes_cmd.finish(MessageSegment.image(path.read_bytes()))

    total = await memes.count()
    meme = await memes.random_meme()
    if meme is not None:
        await memes_cmd.finish(
            MessageSegment.text(f"🐷 当前有 {total} 张猪猪表情包\n")
            + MessageSegment.image(meme.read_bytes())
        )
    await memes_cmd.finish(
        f"🐷 当前有 {total} 张猪猪表情包\n"
        "投稿：发图后回复 /猪猪表情 上传\n"
        "总览：/猪猪表情 列表（可加页码）"
    )



def _bar(pct: int) -> str:
    filled = round(BAR_LEN * pct / 100)
    return "▰" * filled + "▱" * (BAR_LEN - filled)


ach = on_command("猪猪成就", aliases={"猪猪里程碑", "猪猪图鉴成就"}, priority=5, block=True)


@ach.handle()
async def handle_achievement(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    newly, gained = await check_and_claim(uid)

    owned = (await _owned_count(uid))
    rec = await get_user(uid)
    view = progress_view(owned, [int(x) for x in rec.get("claimed", []) if str(x).isdigit()])

    lines = [
        "🐷 猪猪成就",
        "────────────────",
        f"图鉴  {view['owned']}/230（{view['pct']}%） {_bar(view['pct'])}",
        f"称号  {view['current_title']}",
    ]
    if view["next"]:
        n = view["next"]
        lines.append(f"下一档  {n['threshold']} 只 · 还差 {n['threshold'] - view['owned']} 只 → {n['title']} +{n['kleos']} 🏛")
    else:
        lines.append("下一档  已集齐全图鉴！")

    if view["claimed"]:
        claimed_titles = [t for th, _, t in ACHIEVEMENTS if th in view["claimed"]]
        lines.append(f"已领取  {len(view['claimed'])}/{len(ACHIEVEMENTS)} 档（{'、'.join(claimed_titles)}）")

    if newly:
        lines += ["────────────────", "🎉 本次新解锁："]
        for item in newly:
            lines.append(f"  · {item['title']}（{item['threshold']} 只） +{item['kleos']} 🏛")
        lines.append(f"合计 +{gained} 🏛 Kleos（已自动入账）")
    else:
        lines += ["────────────────", f"累计可领 {TOTAL_KLEOS} 🏛 Kleos · 已领 {sum(k for th, k, _ in ACHIEVEMENTS if th in view['claimed'])}"]

    await ach.finish("\n".join(lines))


async def _owned_count(uid: str) -> int:
    from nonebot_plugin_rollpig_plus.store import store as rollpig_store

    state = await rollpig_store.get_draw_state(uid)
    return len(state.pig_ids)


# ---------------------------------------------------------------- /小猪卡
card_cmd = on_command(
    "小猪卡",
    aliases={"小猪dxpass", "猪猪卡", "小猪通行证", "今日小猪卡"},
    priority=5,
    block=True,
)


@card_cmd.handle()
async def handle_card(event: MessageEvent) -> None:
    import asyncio

    from nonebot_plugin_rollpig_plus.resource_manager import pig_resource_manager
    from nonebot_plugin_rollpig_plus.roll_flow import resolve_daily_pig

    from .card import CITIES, pick_music, render_pigpass_card
    from .rarity import rarity_of

    uid = str(event.get_user_id())
    gid = str(getattr(event, "group_id", "") or "")
    try:
        res = await resolve_daily_pig(uid, gid, include_progress=True)
    except Exception as e:
        logger.warning(f"[pigverse] 取今日小猪失败：{e}")
        await card_cmd.finish("小猪数据暂时取不到，稍后再试")
    if not res.pig:
        await card_cmd.finish("先抽今日小猪吧：/今日小猪")

    appearance = pig_resource_manager.resolve_pig_appearance(res.pig, res.ex_level or 0)
    img_path = appearance.image_path or appearance.base_image_path
    player = getattr(getattr(event, "sender", None), "card", "") or str(event.get_user_id())

    try:
        png = await asyncio.to_thread(
            render_pigpass_card,
            pig_name=str(res.pig.get("name") or "小猪"),
            pig_image_path=img_path,
            player_name=player,
            ex_level=int(res.ex_level or 0),
            rarity=rarity_of(str(res.pig.get("id") or "")),
            music=pick_music(),
            city=random.choice(CITIES),
        )
    except Exception as e:
        logger.exception(f"[pigverse] 卡面渲染失败：{e}")
        await card_cmd.finish("卡面渲染失败了，稍后再试")

    await card_cmd.finish(MessageSegment.image(png))


# ---------------------------------------------------------------- /斗猪
arena_cmd = on_command("斗猪", aliases={"斗兽场", "猪斗"}, priority=5, block=True)


@arena_cmd.handle()
async def handle_arena(event: MessageEvent) -> None:
    from .arena import (
        ARENA_DAILY_LIMIT,
        arena_used,
        compare,
        consume_arena,
        pick_text,
        today_pig,
    )

    uid = str(event.get_user_id())
    target = None
    for seg in event.get_message():
        if seg.type == "at":
            qq = str(seg.data.get("qq") or "")
            if qq and qq != str(event.self_id):
                target = qq
    if not target:
        await arena_cmd.finish("用法：/斗猪 @群友（双方都要先抽今日小猪）")
    if target == uid:
        await arena_cmd.finish("不能和自己斗猪哦")

    used = await arena_used(uid)
    if used >= ARENA_DAILY_LIMIT:
        await arena_cmd.finish(f"今天已经斗了 {ARENA_DAILY_LIMIT} 次，明天再来吧")

    gid = str(getattr(event, "group_id", "") or "")
    mine = await today_pig(uid, gid)
    theirs = await today_pig(target, gid)
    if mine is None:
        await arena_cmd.finish("你还没抽今日小猪：/今日小猪")
    if theirs is None:
        await arena_cmd.finish("对方还没抽今日小猪，等他抽完再来")

    await consume_arena(uid)
    result = compare(mine, theirs)
    verdict = "🏆 你赢了！" if result > 0 else ("💀 你输了…" if result < 0 else "🤝 平局")
    lines = [
        "🐷⚔️ 小猪斗兽场",
        "────────────────",
        f"你：{mine['name']} [{mine['rarity']}] EX{mine['ex']}",
        f"对方：{theirs['name']} [{theirs['rarity']}] EX{theirs['ex']}",
        "────────────────",
        verdict,
        pick_text(result, random),
        f"今日剩余 {ARENA_DAILY_LIMIT - used - 1} 次",
    ]
    await arena_cmd.finish("\n".join(lines))


# ---------------------------------------------------------------- /牧场 /喂食
ranch_cmd = on_command("牧场", aliases={"养猪场"}, priority=5, block=True)


@ranch_cmd.handle()
async def handle_ranch(event: MessageEvent) -> None:
    from nonebot_plugin_rollpig_plus.resource_manager import pig_resource_manager

    from .ranch import FEED_PRICE, drachma_balance, feed_level

    uid = str(event.get_user_id())
    ranch = await pigstore.get_ranch(uid)
    balance = await drachma_balance(uid)
    lines = [
        "🐷 雅典养猪场",
        "────────────────",
        f"德拉克马  {balance:,} 🪙",
        f"饲料  {FEED_PRICE} 🪙 / 份（每只猪每日限喂 1 次）",
    ]
    if not ranch:
        lines.append("还没有喂过猪，/喂食 <猪名> 试试")
    else:
        lines.append("已喂养：")
        for pid, rec in list(ranch.items())[:8]:
            pig = pig_resource_manager.pig_map.get(pid)
            name = str(pig.get("name") or pid) if pig else pid
            fed = int(rec.get("fed", 0))
            lines.append(f"  {name}  好感 Lv.{feed_level(fed)}（喂了 {fed} 次）")
    lines.append("────────────────")
    lines.append("用法：/喂食 <猪名>")
    await ranch_cmd.finish("\n".join(lines))


feed_cmd = on_command("喂食", aliases={"喂猪"}, priority=5, block=True)


@feed_cmd.handle()
async def handle_feed(event: MessageEvent, arg: Message = CommandArg()) -> None:
    from nonebot_plugin_rollpig_plus.resource_manager import pig_resource_manager
    from nonebot_plugin_rollpig_plus.store import store as rollpig_store

    from .ranch import FEED_PRICE, drachma_balance, feed, feed_level

    uid = str(event.get_user_id())
    name = arg.extract_plain_text().strip()
    if not name:
        await feed_cmd.finish("用法：/喂食 <猪名>（先 /牧场 看看有哪些猪）")

    state = await rollpig_store.get_draw_state(uid)
    matches: list[tuple[str, str]] = []
    for pid in state.pig_ids:
        pig = pig_resource_manager.pig_map.get(pid)
        if not pig:
            continue
        pname = str(pig.get("name") or "")
        if pname and (name == pname or name in pname or pname in name):
            matches.append((pid, pname))
    if not matches:
        await feed_cmd.finish("你猪圈里没有叫这个的猪，/我的猪圈 看看有哪些")
    if len(matches) > 1:
        names = "、".join(n for _, n in matches[:6])
        await feed_cmd.finish(f"有多个匹配：{names}，说得更具体一点")

    pid, pname = matches[0]
    result = await feed(uid, pid)
    if not result["ok"]:
        if result["reason"] == "today":
            await feed_cmd.finish(f"今天已经喂过 {pname} 了，明天再来")
        await feed_cmd.finish(f"德拉克马不足，喂一次需要 {FEED_PRICE} 🪙")

    await feed_cmd.finish(
        f"🌾 你喂了 {pname} 一筐饲料，它满足地哼唧了一声。\n"
        f"好感 Lv.{feed_level(result['fed'])}（{result['fed']} 次）"
        f" · 剩余 {await drachma_balance(uid):,} 🪙"
    )


# ---------------------------------------------------------------- /今日猪猪（复用 rollpig 抽猪流程）
from nonebot_plugin_rollpig_plus.helpers import (  # noqa: E402
    get_event_group_id,
    guard_group_enabled,
    guard_store_errors,
    send_rendered_pig,
)
from nonebot_plugin_rollpig_plus.reservation_flow import (  # noqa: E402
    deliver_newly_ready_reservations,
)
from nonebot_plugin_rollpig_plus.roll_flow import (  # noqa: E402
    RECORDED_PIG_RESOURCE_MISSING_TEXT,
    resolve_daily_pig,
)

pigpig_cmd = on_command("今日猪猪", aliases={"今日猪"}, priority=5, block=True)


@pigpig_cmd.handle()
@guard_group_enabled(pigpig_cmd)
@guard_store_errors(pigpig_cmd)
async def handle_pigpig_cmd(event: MessageEvent) -> None:
    resolution = await resolve_daily_pig(
        str(event.user_id), get_event_group_id(event), include_progress=True
    )
    if resolution.recorded_pig_missing:
        await pigpig_cmd.finish(RECORDED_PIG_RESOURCE_MISSING_TEXT)
    if resolution.missing_resources or not resolution.pig:
        await pigpig_cmd.finish("猪圈塌房了（数据缺失）")
    await send_rendered_pig(
        pigpig_cmd,
        event,
        resolution.pig,
        extra_text=resolution.growth_text,
        ex_level=resolution.ex_level or 0,
        after_send=(
            (lambda: deliver_newly_ready_reservations(str(event.self_id)))
            if resolution.was_auto_created
            else None
        ),
    )


# ---------------------------------------------------------------- /吃什么 /喝什么
eat_cmd = on_command(
    "吃什么",
    aliases={"吃啥", "今天吃什么", "午饭吃什么", "晚饭吃什么", "早饭吃什么", "夜宵吃什么"},
    priority=5,
    block=True,
)
drink_cmd = on_command(
    "喝什么",
    aliases={"喝啥", "今天喝什么", "喝点什么", "饮品"},
    priority=5,
    block=True,
)

EAT_LEADS = ("就它了！", "别纠结了，", "今天翻牌子：", "天意如此，", "听我的，")
DRINK_LEADS = ("来一杯", "解渴就选", "今天喝点", "推荐", "就喝")

MENU_WORDS = ("列表", "分类", "都有啥", "菜单", "有什么", "有啥")


def _eat_menu() -> str:
    cuisines = dishes.cuisines()
    chain_stats = dishes.chains()
    lines = [f"🍽 菜谱共 {dishes.count()} 道（{len(cuisines)} 个菜系）"]
    for cuisine, n in cuisines.items():
        lines.append(f"  {cuisine} {n} 道")
    if chain_stats:
        lines.append(f"🏪 连锁店 {len(chain_stats)} 家：")
        for store, n in chain_stats.items():
            lines.append(f"  {store} {n} 项")
    lines.append("用法：/吃什么 [菜系|店家] 例：/吃什么 川菜、/吃什么 麦当劳")
    return "\n".join(lines)


def _drink_menu() -> str:
    stats = dishes.drink_categories()
    lines = [f"🥤 饮品共 {sum(stats.values())} 种："]
    for cat, n in stats.items():
        lines.append(f"  {cat} {n} 种")
    lines.append("用法：/喝什么 [分类|品牌] 例：/喝什么 奶茶、/喝什么 瑞幸咖啡")
    return "\n".join(lines)


@eat_cmd.handle()
async def handle_eat(event: MessageEvent, arg: Message = CommandArg()) -> None:
    sub = arg.extract_plain_text().strip()

    if not sub:
        dish = dishes.random_dish()
        if dish is None or dish.get("path") is None:
            await eat_cmd.finish("菜谱还没准备好，稍后再试")
        text = (
            f"🍽 {random.choice(EAT_LEADS)}今天吃「{dish['name']}」吧！\n"
            f"{dish['cuisine']} · 再发一次换一道"
        )
        await eat_cmd.finish(
            MessageSegment.text(text) + MessageSegment.image(dish["path"].read_bytes())
        )

    if sub in MENU_WORDS:
        await eat_cmd.finish(_eat_menu())

    target = dishes.find_target(sub)
    if target is None:
        await eat_cmd.finish(f"没找到「{sub}」，可以试试：\n{_eat_menu()}")

    kind, name = target
    if kind == "store":
        pick = dishes.random_store_item(name)
        if pick is None:
            await eat_cmd.finish(f"「{name}」的菜单还没收录")
        await eat_cmd.finish(
            f"🏪 {random.choice(EAT_LEADS)}{name}的「{pick['name']}」！\n再发一次换一道"
        )

    if kind == "drink":
        await eat_cmd.finish(f"「{name}」是喝的，试试 /喝什么 {name}")

    dish = dishes.random_dish(name)
    if dish is None or dish.get("path") is None:
        await eat_cmd.finish(f"「{name}」暂时没有图，换一道吧")
    text = (
        f"🍽 {random.choice(EAT_LEADS)}今天吃「{dish['name']}」吧！\n"
        f"{dish['cuisine']} · 再发一次换一道"
    )
    await eat_cmd.finish(
        MessageSegment.text(text) + MessageSegment.image(dish["path"].read_bytes())
    )


@drink_cmd.handle()
async def handle_drink(event: MessageEvent, arg: Message = CommandArg()) -> None:
    sub = arg.extract_plain_text().strip()

    if sub in MENU_WORDS:
        await drink_cmd.finish(_drink_menu())

    if sub:
        target = dishes.find_target(sub)
        if target is None:
            await drink_cmd.finish(f"没找到「{sub}」，可以试试：\n{_drink_menu()}")
        kind, name = target
        if kind == "store":
            await drink_cmd.finish(f"「{name}」是吃饭的地方，试试 /吃什么 {name}")
        if kind == "cuisine":
            await drink_cmd.finish(f"「{name}」是菜系，试试 /吃什么 {name}")

    pick = dishes.random_drink(sub)
    if pick is None:
        await drink_cmd.finish("饮品库还没准备好，稍后再试")
    await drink_cmd.finish(
        f"🥤 {random.choice(DRINK_LEADS)}「{pick['name']}」吧！\n{pick['category']} · 再发一次换一杯"
    )

"""B站助手：链接解析 + 发视频、排行榜、UP 主查询。

用法
----
1) 群里发 B站链接（b23 短链/BV号/av号）→ 自动解析并给出信息卡，
   再回复「y」即下载压缩后把视频发到群里（默认需要确认，避免刷屏）。
   私聊直接发链接同理。
2) /bilibili                      查看帮助
   /bilibili rank [分区] [条数]   今日排行榜
   /bilibili up <UID 或 昵称>      查询 UP 主数据
   /bilibili <链接或BV号>          解析并询问是否发视频
3) 群聊也可以 `@机器人 /bilibili rank 游戏`，带 @ 时由补偿 matcher 接住。

发视频相关配置（.env.prod）：
   BILI_VIDEO_ENABLE=1       总开关
   BILI_VIDEO_AUTO=1         群里不用 @ 也自动响应链接（0=必须@）
   BILI_VIDEO_GROUPS=        只在这些群自动响应（逗号分隔，空=所有群）
   BILI_VIDEO_CONFIRM=1      先回信息卡+让用户回 y 确认（0=直接发）
   BILI_VIDEO_MAX_DURATION=600  超过这个秒数不发
   BILI_VIDEO_MAX_MB=20      压缩后体积上限（MB）
   BILI_VIDEO_HEIGHT=480     压缩后最大高度
   BILI_VIDEO_CRF=30         越大越糊、越小越清晰
   BILI_VIDEO_QN=64          下载源画质（64=720P，32=480P）
   BILI_VIDEO_TTL=600        确认有效期（秒）

为什么不用现成插件
------------------
调研过 mengshouer/nonebot_plugin_analysis_bilibili（停更 2024-12）与
Ikaros-521/nonebot_plugin_searchBiliInfo（停更 2023-12）：都不做「下载视频发群」，
且最后更新早于 B站风控升级。故自研，只依赖项目已有的 httpx + ffmpeg。
"""
import json
import os
import re
import time
from pathlib import Path

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.typing import T_State

from . import sender
from .api import (
    BiliError,
    BiliRiskError,
    NotVideoLink,
    SECTIONS,
    extract_target,
    fetch_ranking,
    fetch_up_card,
    fetch_video,
    search_up,
)

__plugin_meta__ = PluginMetadata(
    name="B站助手",
    description="B站链接解析、压缩视频发群、排行榜、UP主查询",
    usage="群里发B站链接→回 y 发视频；/bilibili rank 看榜单；/bilibili up 影视飓风 查UP主",
)

_CQ_CODE = re.compile(r"\[CQ:[^\]]*\]")
MAX_RANK = 20

# ---------------- 视频发送相关的运行时配置 ----------------
CONFIRM = os.getenv("BILI_VIDEO_CONFIRM", "1") == "1"
AUTO = os.getenv("BILI_VIDEO_AUTO", "1") == "1"
AUTO_GROUPS = {g.strip() for g in os.getenv("BILI_VIDEO_GROUPS", "").split(",") if g.strip()}
CONFIRM_TTL = int(os.getenv("BILI_VIDEO_TTL", "600"))

# 关闭了视频解析的群（持久化到 data/bili_video_groups.json）
_STATE_FILE = Path(os.getenv("BILI_VIDEO_STATE", "data/bili_video_groups.json"))


def _load_state() -> set:
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text("utf-8"))
            return {str(g) for g in (data.get("off") or [])}
    except Exception as e:
        logger.warning(f"[bilibili] 读取群开关失败: {e}")
    return set()


def _save_state() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"off": sorted(_GROUP_OFF)}, ensure_ascii=False), "utf-8")
    except Exception as e:
        logger.warning(f"[bilibili] 保存群开关失败: {e}")


_GROUP_OFF: set = _load_state()

# 待确认的请求：chat_key -> {meta, user, expire}
_PENDING: dict = {}
# 防重复： (chat_key, bvid) -> 时间戳
_RECENT: dict = {}
_RECENT_TTL = 120

# 回 y / y 720p / y 1080p（可带画质；不带则默认压缩版 480P）
_CONFIRM_RE = re.compile(
    r"^(?:y|yes|好|要|发|发送|确认|可以|行|嗯|发吧)(?:\s*(360|480|720|1080)\s*p?)?$",
    re.I,
)

HELP = (
    "📺 B站助手用法\n"
    "────────────\n"
    "① 群里发B站链接 → 我给出信息卡，回「y」就把压缩版视频发到群里\n"
    "   想要更高画质：回「y 720p」或「y 1080p」（需配 B 站账号 Cookie）\n"
    "   私聊直接发链接，同样回 y\n"
    "② /bilibili rank [分区] [条数] —— 今日排行榜\n"
    f"   分区可选：{'/'.join(SECTIONS)}\n"
    "   例：/bilibili rank 动画 5\n"
    "③ /bilibili up <UID 或 昵称> —— 查UP主\n"
    "   例：/bilibili up 946974  或  /bilibili up 影视飓风\n"
    "④ /bilibili + 链接/BV号 —— 解析并询问是否发视频\n"
    "⑤ @我 关闭本群视频解析 / 开启本群视频解析 —— 群主或管理员可开关"
)


def _clean(text: str) -> str:
    """去掉 CQ 码。群聊 @机器人时消息串会是 `[CQ:at,qq=xxx] 正文`，
    不清掉会让链接提取和命令匹配全部失效（custom_cmd 插件也是这么处理的）。"""
    return _CQ_CODE.sub("", text or "").strip()


def _at_me(event) -> bool:
    """是否 @ 了机器人（含回复触发的 to_me）。"""
    try:
        for seg in event.message:
            if seg.type == "at" and str(seg.data.get("qq")) == str(event.self_id):
                return True
    except Exception:
        pass
    return bool(getattr(event, "to_me", False))


def _message_text(event) -> str:
    """取消息里可用于提取链接的文本。

    除了普通文本，还要处理 QQ 小程序/卡片：
      [CQ:json,data={..."qqdocurl":"https:\\/\\/b23.tv\\/o0vV2Do"...}]
    JSON 在消息里是 `\/` 转义的，必须还原成 `/`，否则 b23.tv 正则匹配不到。
    """
    parts = [_clean(str(event.get_message()))]
    try:
        for seg in event.message:
            if seg.type == "json":
                raw = str(seg.data.get("data") or "")
            elif seg.type == "xml":
                raw = str(seg.data.get("xml") or seg.data.get("data") or "")
            else:
                continue
            # 卡片 JSON 里是 `https:\/\/b23.tv\/xxx`，单/双反斜杠都归一
            parts.append(re.sub(r"\\+/", "/", raw))
    except Exception:
        pass
    return "\n".join(parts)


def _chat_key(event) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"g:{event.group_id}"
    return f"p:{event.user_id}"


def _auto_allowed(event) -> bool:
    """群里是否允许不 @ 也自动响应。"""
    if not isinstance(event, GroupMessageEvent):
        return True
    if not AUTO:
        return _at_me(event)
    if AUTO_GROUPS and str(event.group_id) not in AUTO_GROUPS:
        return _at_me(event)
    return True


def fmt(n) -> str:
    """数字转 万/亿。"""
    if n is None:
        return "未知"
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def _dur(sec: int) -> str:
    try:
        sec = int(sec)
    except Exception:
        return "?"
    return f"{sec // 60}:{sec % 60:02d}"


async def card_from_meta(v: dict, fans=None) -> str:
    lines = [
        f"📺 {v['title']}",
        f"👤 UP：{v['owner_name']}（UID {v['owner_mid']}）"
        f"{'　粉丝 ' + fmt(fans) if fans is not None else ''}",
        f"⏱ 时长 {_dur(v['duration'])}",
        "",
        f"▶️ 播放 {fmt(v['view'])}　💬 弹幕 {fmt(v['danmaku'])}　💭 评论 {fmt(v['reply'])}",
        f"👍 点赞 {fmt(v['like'])}　🪙 投币 {fmt(v['coin'])}",
        f"⭐ 收藏 {fmt(v['favorite'])}　🔁 分享 {fmt(v['share'])}",
        f"🔗 https://www.bilibili.com/video/{v['bvid']}",
    ]
    desc = v["desc"]
    if desc:
        lines.append("")
        lines.append(f"简介：{desc[:100]}{'…' if len(desc) > 100 else ''}")
    return "\n".join(lines)


async def video_report(key: str, value: str) -> str:
    """只要信息卡（不发视频）。@触发与命令触发共用，保证输出一致。"""
    try:
        v = await fetch_video(key, value)
    except BiliError as e:
        return f"解析失败：{e}"
    except Exception as e:
        logger.exception(f"[bilibili] 视频解析异常: {e}")
        return "解析时出错了，稍后再试~"

    # 顺带拿 UP 主粉丝数。失败不影响主结果。
    fans = None
    try:
        card = await fetch_up_card(v["owner_mid"])
        fans = card.get("follower") or card.get("fans")
    except Exception as e:
        logger.warning(f"[bilibili] 取UP主粉丝数失败（不影响主结果）: {e}")
    return await card_from_meta(v, fans)


def _prune() -> None:
    now = time.time()
    for k, p in list(_PENDING.items()):
        if p["expire"] < now:
            _PENDING.pop(k, None)
    for k, ts in list(_RECENT.items()):
        if ts + _RECENT_TTL < now:
            _RECENT.pop(k, None)


async def process(event, bot, meta: dict, height: int | None = None) -> bool:
    """真正执行：下载 → 压缩 → 发视频。成功返回 True。"""
    if sender.busy():
        await bot.send(event, "我还在处理上一个视频，等它发完再来～")
        return False
    path = None
    try:
        path, actual = await sender.render(meta, height=height)
        seg = MessageSegment.video(sender.video_segment_string(path))
        await bot.send(event, seg)
        if height and actual and actual < height:
            tip = "" if sender.has_account() else "（在 .env 配置 BILI_SESSDATA 可解锁 720P/1080P）"
            await bot.send(event, f"⚠️ 这个视频最高只有 {actual}P，已按 {actual}P 发送{tip}")
        return True
    except sender.VideoSkip as e:
        await bot.send(event, f"⚠️ {e}")
        return False
    except BiliError as e:
        await bot.send(event, f"❌ 处理失败：{e}（可以再回一次 y 重试）")
        return False
    except Exception as e:
        logger.exception(f"[bilibili] 发送视频异常: {e}")
        await bot.send(event, "❌ 发送视频出错了（可以再回一次 y 重试）")
        return False
    finally:
        if path is not None:
            sender.cleanup(path)


async def start_flow(event, bot, key: str, value: str) -> None:
    """链接入口：拉信息 → 决定直接发还是等确认。"""
    _prune()
    if not sender.is_enabled():
        await _send(event, bot, await video_report(key, value))
        return
    try:
        meta = await sender.prepare(key, value)
    except sender.VideoSkip as e:
        card = await video_report(key, value)
        await _send(event, bot, (card + "\n\n" if card else "") + f"⚠️ {e}")
        return
    except NotVideoLink as e:
        # 群里自动响应时，直播/专栏/动态链接直接忽略，不打扰
        if isinstance(event, GroupMessageEvent) and AUTO and _auto_allowed(event):
            return
        await _send(event, bot, f"⚠️ {e}")
        return
    except BiliError as e:
        await _send(event, bot, f"解析失败：{e}")
        return
    except Exception as e:
        logger.exception(f"[bilibili] 准备发送失败: {e}")
        await _send(event, bot, "解析时出错了，稍后再试～")
        return

    ck = _chat_key(event)
    bvid = meta["bvid"]
    if _RECENT.get((ck, bvid), 0) + _RECENT_TTL > time.time():
        return  # 同一个视频短时间内重复发链接，不重复响应
    _RECENT[(ck, bvid)] = time.time()

    card = await video_report(key, value)
    if not CONFIRM:
        await _send(event, bot, card)
        await process(event, bot, meta)
        return
    _PENDING[ck] = {"meta": meta, "user": event.user_id,
                    "expire": time.time() + CONFIRM_TTL}
    est = sender.estimate_mb(meta["duration"])
    how = "回「y」发 480P 压缩版"
    if sender.has_account():
        how += "；要高画质回「y 720p」或「y 1080p」"
    else:
        how += "（配 BILI_SESSDATA 后可发 720P/1080P）"
    await _send(
        event, bot,
        f"{card}\n\n{how}，约 {est:g}MB，{CONFIRM_TTL // 60} 分钟内有效",
    )


async def _send(event, bot, text: str) -> None:
    await bot.send(event, text)


def _is_group_admin(event) -> bool:
    try:
        if str(event.user_id) in {str(u) for u in get_driver().config.superusers}:
            return True
        return str(getattr(event.sender, "role", "") or "") in ("owner", "admin")
    except Exception:
        return False


async def _toggle_video(event, bot, on: bool) -> None:
    if not isinstance(event, GroupMessageEvent):
        await _send(event, bot, "这个开关只能在群里用～")
        return
    if not _is_group_admin(event):
        await _send(event, bot, "只有群主、管理员或 bot 主能开关本群视频解析。")
        return
    gid = str(event.group_id)
    if on:
        _GROUP_OFF.discard(gid)
    else:
        _GROUP_OFF.add(gid)
    _save_state()
    logger.info(f"[bilibili] 群 {gid} 视频解析 {'开启' if on else '关闭'}")
    if on:
        await _send(event, bot, "✅ 已开启本群视频解析（发链接会解析，回 y 发视频）")
    else:
        await _send(event, bot, "✅ 已关闭本群视频解析（本群发链接不再响应，私聊不受影响）")


_OFF_WORDS = {"off", "关", "关闭", "disable", "关闭视频", "关闭解析", "关掉"}
_ON_WORDS = {"on", "开", "开启", "enable", "开启视频", "开启解析", "打开"}


async def _run_command_flow(event, bot, text: str) -> None:
    """命令入口：开关 / 有链接发视频 / rank、up、help。"""
    low = (text or "").strip().lower()
    if low in _OFF_WORDS:
        await _toggle_video(event, bot, False)
        return
    if low in _ON_WORDS:
        await _toggle_video(event, bot, True)
        return
    target = extract_target(text)
    if target:
        await start_flow(event, bot, *target)
        return
    await _send(event, bot, await dispatch(text))


# ---------- ① 链接 → 流程 ----------
async def _link_rule(event, state: T_State) -> bool:
    if isinstance(event, GroupMessageEvent):
        if str(event.group_id) in _GROUP_OFF:
            return False
        if not _auto_allowed(event):
            return False
    text = _message_text(event).strip()
    # /bilibili <link> 由命令 matcher 处理，避免两条 matcher 同时响应
    if re.match(r"^/?(bilibili|bili|B站|b站)\b", text, re.I):
        return False
    target = extract_target(text)
    if not target:
        return False
    state["bili_target"] = target
    return True


bili_parse = on_message(rule=Rule(_link_rule), priority=3, block=True)


@bili_parse.handle()
async def _handle_parse(event, bot, state: T_State):
    key, value = state["bili_target"]
    await start_flow(event, bot, key, value)


# ---------- ② 确认「y」→ 发视频 ----------
async def _confirm_rule(event, state: T_State) -> bool:
    if isinstance(event, GroupMessageEvent) and str(event.group_id) in _GROUP_OFF:
        return False
    ck = _chat_key(event)
    p = _PENDING.get(ck)
    if not p or p["expire"] < time.time():
        return False
    if str(event.user_id) != str(p["user"]):
        return False
    text = _clean(event.get_plaintext()).strip().lower()
    m = _CONFIRM_RE.match(text)
    if not m:
        return False
    state["bili_meta"] = p["meta"]
    state["bili_height"] = int(m.group(1)) if m.group(1) else None
    return True


bili_confirm = on_message(rule=Rule(_confirm_rule), priority=2, block=True)


@bili_confirm.handle()
async def _handle_confirm(event, bot, state: T_State):
    if sender.busy():
        await bili_confirm.finish("我还在处理上一个视频，等它发完再回 y 就行～")
    ok = await process(event, bot, state["bili_meta"], state.get("bili_height"))
    if ok:
        # 失败时保留 pending，用户可以再回一次 y 重试
        _PENDING.pop(_chat_key(event), None)


# ---------- 命令分发 ----------
async def dispatch(arg: str) -> str:
    arg = (arg or "").strip()
    if not arg or arg.lower() in ("help", "帮助", "?", "h"):
        return HELP
    low = arg.lower()

    # 排行榜
    if low.startswith(("rank", "榜单", "排行", "ph", "ranking")):
        parts = arg.split()
        section, count = "全站", 10
        for p in parts[1:]:
            if p.isdigit():
                count = max(1, min(int(p), MAX_RANK))
            elif p in SECTIONS:
                section = p
        try:
            rows = await fetch_ranking(section, count)
            note = ""
        except BiliRiskError:
            # 分区榜走 wbi 签名的 ranking/v2，最容易被频率风控；
            # 全站榜走 popular 接口更稳 —— 分区挂了就降级给全站榜。
            if section == "全站":
                return "查榜单失败：B站临时风控（请求太频繁），歇 30 秒再试~"
            try:
                rows = await fetch_ranking("全站", count)
            except Exception as e:
                logger.warning(f"[bilibili] 分区榜风控且全站降级也失败: {e}")
                return "查榜单失败：B站临时风控（请求太频繁），歇 30 秒再试~"
            note = f"⚠️「{section}」分区榜这会儿被 B站风控了，先给你全站榜：\n"
            section = "全站"
        except BiliError as e:
            return f"查榜单失败：{e}"
        except Exception as e:
            logger.exception(f"[bilibili] 榜单异常: {e}")
            return "查榜单时出错了，稍后再试~"
        if not rows:
            return f"没拿到「{section}」的榜单数据。"
        head = f"📊 B站{section}排行榜 TOP{len(rows)}\n────────────"
        body = [
            f"{r['rank']}. {r['title']}\n   UP {r['owner']} · 播放 {fmt(r['view'])} · "
            f"点赞 {fmt(r['like'])}\n   https://www.bilibili.com/video/{r['bvid']}"
            for r in rows
        ]
        return note + "\n".join([head] + body)

    # UP 主查询：显式 up/用户 前缀，或纯 UID
    kw = arg
    # 从原文（非小写）里取关键字，避免把用户昵称的大小写弄丢
    m = re.match(r"^(?:up主|up|uid|用户)\s*(.+)$", low)
    if m:
        kw = arg[len(m.group(0)) - len(m.group(1)):].strip()
    return await _query_up(kw)


async def _query_up(kw: str) -> str:
    kw = (kw or "").strip()
    if not kw:
        return "要查谁？给我 UID 或昵称，例：/bilibili up 影视飓风"

    # 纯数字直接当 UID；否则先搜昵称
    if kw.isdigit():
        mid = int(kw)
        others = ""
    else:
        try:
            results = await search_up(kw)
        except BiliError as e:
            return f"搜索UP主失败：{e}"
        except Exception as e:
            logger.exception(f"[bilibili] 搜索UP异常: {e}")
            return "搜索时出错了，稍后再试~"
        if not results:
            return f"没搜到昵称含「{kw}」的UP主。直接给我 UID 也行。"
        top = results[0]
        mid = top["mid"]
        # 只在存在多个候选时才提示，避免「按昵称匹配到：影视飓风」这种废话
        others = "\n".join(
            f"  ・ {r['name']}（UID {r['mid']}）粉丝 {fmt(r['fans'])}"
            for r in results[1:4]
        )

    try:
        c = await fetch_up_card(mid)
    except BiliError as e:
        return f"查UP主失败：{e}"
    except Exception as e:
        logger.exception(f"[bilibili] UP主查询异常: {e}")
        return "查询时出错了，稍后再试~"

    following = c.get("following") or c.get("attention")
    lines = [
        f"👤 {c['name']}（UID {c['mid']}）",
        "────────────",
        f"粉丝 {fmt(c.get('follower') or c['fans'])}"
        f"　关注 {fmt(following)}"
        f"　稿件 {fmt(c['archive_count'])}",
        f"获赞 {fmt(c['like_num'])}",
        f"🔗 {c['space_url']}",
        "（总播放量：B站已关闭该数据接口，查不到了）",
    ]
    if c.get("sign"):
        lines.insert(2, f"签名：{c['sign'][:50]}")
    if others:
        lines.append(f"ℹ️ 昵称还有其他匹配：\n{others}")
    return "\n".join(lines)


# ---------- ③ 自然语言开关：@机器人 关闭/开启本群视频解析 ----------
async def _toggle_rule(event, state: T_State) -> bool:
    if not isinstance(event, GroupMessageEvent) or not _at_me(event):
        return False
    text = _clean(str(event.get_message())).strip()
    m = re.fullmatch(r"(?:请)?(关闭|禁用|停用|开启|启用|恢复)本群(?:的)?视频解析[。.!！]?", text)
    if not m:
        return False
    state["bili_toggle_on"] = m.group(1) in ("开启", "启用", "恢复")
    return True


bili_toggle = on_message(rule=Rule(_toggle_rule), priority=2, block=True)


@bili_toggle.handle()
async def _handle_toggle(event, bot, state: T_State):
    await _toggle_video(event, bot, state["bili_toggle_on"])


# ---------- ④ 标准命令 /bilibili ----------
bili_cmd = on_command("bilibili", aliases={"bili", "B站", "b站"},
                      priority=3, block=True)


@bili_cmd.handle()
async def _handle_cmd(event, bot, args=CommandArg()):
    text = args.extract_plain_text().strip()
    await _run_command_flow(event, bot, text)


# ---------- ④ 补偿：群聊「@机器人 + /bilibili ...」 ----------
# on_command 要求消息首段是文本且以 / 开头；群聊一旦带了 @，
# 首段变成 at 段，命令规则就匹配不上了 —— 所以这里单独接住。
async def _at_cmd_rule(event, state: T_State) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if not _at_me(event):
        return False
    text = _message_text(event)
    m = re.match(r"^/?(bilibili|bili|B站|b站)\s*(.*)$", text, re.I)
    if not m:
        return False
    state["bili_arg"] = m.group(2).strip()
    return True


bili_at_cmd = on_message(rule=Rule(_at_cmd_rule), priority=3, block=True)


@bili_at_cmd.handle()
async def _handle_at_cmd(event, bot, state: T_State):
    await _run_command_flow(event, bot, state["bili_arg"])


# ---------- ⑤ 启动日志：确认 B站账号 Cookie 是否生效 ----------
@get_driver().on_startup
async def _log_account_status():
    if sender.has_account():
        logger.info("[bilibili] 已配置 BILI_SESSDATA：可请求 720P（1080P 还需大会员）")
    else:
        logger.info("[bilibili] 未配置 BILI_SESSDATA：画质上限 480P（配了才能 y 720p / y 1080p）")

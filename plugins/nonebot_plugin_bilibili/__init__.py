"""B站助手：解析视频链接、查询排行榜、查询 UP 主数据。

用法
----
1) 群聊 @墨璃 + B站链接  → 直接解析视频（私聊直接发链接即可）
2) /bilibili                      查看帮助
   /bilibili rank [分区] [条数]   今日排行榜，如 `/bilibili rank 动画 5`
   /bilibili up <UID 或 昵称>      查询 UP 主数据
   /bilibili <链接或BV号>          解析视频
3) 群聊也可以 `@墨璃 /bilibili rank 游戏`，带 @ 时会被下面的补偿 matcher 接住。

为什么不用现成插件
------------------
调研过 mengshouer/nonebot_plugin_analysis_bilibili（停更 2024-12）与
Ikaros-521/nonebot_plugin_searchBiliInfo（停更 2023-12）：二者都不满足
「@才解析」「排行榜」「UP主统计」这三项，且最后更新早于 B站风控升级，
接进来大概率直接失效。故自研，只依赖项目已有的 httpx。
"""
import re

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.typing import T_State

from .api import (
    BiliError,
    BiliRiskError,
    SECTIONS,
    extract_target,
    fetch_ranking,
    fetch_up_card,
    fetch_video,
    search_up,
)

__plugin_meta__ = PluginMetadata(
    name="B站助手",
    description="解析B站视频链接、查询全站/分区排行榜、查询UP主数据",
    usage="@我+B站链接 解析视频；/bilibili rank 看榜单；/bilibili up 影视飓风 查UP主",
)

_CQ_CODE = re.compile(r"\[CQ:[^\]]*\]")
MAX_RANK = 20

HELP = (
    "📺 B站助手用法\n"
    "────────────\n"
    "① 群里 @我 并发一条B站链接，我就解析（私聊直接发链接）\n"
    "② /bilibili rank [分区] [条数] —— 今日排行榜\n"
    f"   分区可选：{'/'.join(SECTIONS)}\n"
    "   例：/bilibili rank 动画 5\n"
    "③ /bilibili up <UID 或 昵称> —— 查UP主\n"
    "   例：/bilibili up 946974  或  /bilibili up 影视飓风\n"
    "④ /bilibili + 链接/BV号 —— 直接解析视频"
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


async def video_report(key: str, value: str) -> str:
    """解析视频并格式化。@触发与命令触发共用，保证输出一致。"""
    try:
        v = await fetch_video(key, value)
    except BiliError as e:
        return f"解析失败：{e}"
    except Exception as e:
        logger.exception(f"[bilibili] 视频解析异常: {e}")
        return "解析时出错了，稍后再试~"

    # 顺带拿 UP 主粉丝数（用户明确要这项）。失败不影响主结果。
    fans = None
    try:
        card = await fetch_up_card(v["owner_mid"])
        fans = card.get("follower") or card.get("fans")
    except Exception as e:
        logger.warning(f"[bilibili] 取UP主粉丝数失败（不影响主结果）: {e}")

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


# ---------- ① @ + 链接 → 解析视频 ----------
async def _parse_rule(event, state: T_State) -> bool:
    target = extract_target(_clean(str(event.get_message())))
    if not target:
        return False
    # 群聊必须 @ 机器人；私聊天然是对着她说
    if isinstance(event, GroupMessageEvent) and not _at_me(event):
        return False
    state["bili_target"] = target
    return True


bili_parse = on_message(rule=Rule(_parse_rule), priority=3, block=True)


@bili_parse.handle()
async def _handle_parse(state: T_State):
    key, value = state["bili_target"]
    await bili_parse.finish(await video_report(key, value))


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

    # 视频链接/BV号
    target = extract_target(arg)
    if target:
        key, value = target
        return await video_report(key, value)

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


# ---------- ② 标准命令 /bilibili ----------
bili_cmd = on_command("bilibili", aliases={"bili", "B站", "b站"},
                      priority=3, block=True)


@bili_cmd.handle()
async def _handle_cmd(args=CommandArg()):
    await bili_cmd.finish(await dispatch(args.extract_plain_text().strip()))


# ---------- ③ 补偿：群聊「@机器人 + /bilibili ...」 ----------
# on_command 要求消息首段是文本且以 / 开头；群聊一旦带了 @，
# 首段变成 at 段，命令规则就匹配不上了 —— 所以这里单独接住。
async def _at_cmd_rule(event, state: T_State) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if not _at_me(event):
        return False
    text = _clean(str(event.get_message()))
    m = re.match(r"^/?(bilibili|bili|B站|b站)\s*(.*)$", text, re.I)
    if not m:
        return False
    state["bili_arg"] = m.group(2).strip()
    return True


bili_at_cmd = on_message(rule=Rule(_at_cmd_rule), priority=3, block=True)


@bili_at_cmd.handle()
async def _handle_at_cmd(state: T_State):
    await bili_at_cmd.finish(await dispatch(state["bili_arg"]))

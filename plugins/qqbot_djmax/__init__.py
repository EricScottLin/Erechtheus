"""DJMAX Respect V 查分插件（NoneBot2 / OneBot v11）。

移植自 YoisakiKnd/entari 的 ``plugins/djmax``，功能一一对应：

    绑定 / 解绑      V-Archive ID
    b100  list  pack  new  pp  max    成绩分表
    info   单曲信息：封面、曲名、歌手、曲包 + 全部键数定数
    song   单曲成绩：该曲该键数的分数、DJPower、MC/PP

数据来自 V-Archive（https://v-archive.net/），出图库为
djmax_bests（https://github.com/SoreHait/djmax_bests_generate）。

触发方式：与其他插件一致，统一用 ``/`` 或命令头；``dj`` 与 ``djmax`` 均可。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from . import bind_store, render
from .config import Config, config
from .djmax_bests import api_handler, song_index, single_query
from .djmax_bests.models import BMODES, DIFFS, DMSongDBEntry

__plugin_meta__ = PluginMetadata(
    name="DJMAX 查分",
    description="DJMAX Respect V 查分：B100、分表、单曲信息与单曲成绩（V-Archive）",
    usage="""\
dj bind <V-Archive ID>   绑定
dj unbind                解绑
dj b100 4                B100 分表
dj list 4 15             指定等级分表
dj pack 4 MX VC10        曲包分表
dj new 4 MX              新曲分表
dj pp 4                  Perfect Play 分表
dj max 4                 理论值 B100
dj song <歌曲>            曲目信息（全部键数定数）
dj song 4B <歌曲>         只看 4B
dj info 4B <歌曲>         单曲成绩
dj listdlc               曲包代码列表""",
    homepage="https://github.com/YoisakiKnd/entari",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

#: 触发词；消息首词命中其一才算 DJMAX 命令。
NAMESPACES = ("dj", "djmax")

CMD_BIND = "bind"
CMD_UNBIND = "unbind"
CMD_BESTS = "b100"
CMD_LIST = "list"
CMD_PACK = "pack"
CMD_LISTDLC = "listdlc"
CMD_HELP = "help"
CMD_NEW = "new"
CMD_PP = "pp"
CMD_MAX = "max"
CMD_SONG = "song"
CMD_INFO = "info"

#: 曲目信息（封面/歌手/曲包/定数）：主名 song，其余为中文别名。
_ALIAS_SONG = {CMD_SONG, "查歌", "曲目", "单曲信息"}
#: 个人成绩：主名 info，其余为别名。
_ALIAS_SCORE = {CMD_INFO, "score", "成绩", "查分", "单曲成绩", "查询"}
_ALIAS_HELP = {CMD_HELP, "帮助", "help"}

VALID_BMODES = set(BMODES)

#: 消息里可能混入的不可见字符，统一当空格。
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")
_LEVEL_RE = re.compile(r"^(sc)?\s*(\d+)$", re.IGNORECASE)

#: 曲库与曲包名的进程内缓存。
_index: song_index.SongIndex | None = None
_dlc_names: dict[str, str] | None = None


def build_help_text() -> str:
    return (
        "DJMAX 命令（dj 与 djmax 都可用）：\n"
        "【账号】\n"
        "  dj bind <V-Archive ID>   绑定（填 V-Archive 注册 ID，不是游戏 ID）\n"
        "  dj unbind                解绑\n"
        "【成绩】\n"
        "  dj b100 4                B100 分表（4/5/6/8）\n"
        "  dj list 4 15             指定等级分表；sc15 查 SC 侧\n"
        "  dj pack 4 MX VC10        曲包分表（难度 NM/HD/MX/SC）\n"
        "  dj new 4 MX              新曲分表\n"
        "  dj pp 4                  Perfect Play 分表\n"
        "  dj max 4                 理论值 B100\n"
        "  dj listdlc               曲包代码列表\n"
        "【单曲】\n"
        "  dj song <歌曲>            曲目信息：全部键数定数\n"
        "  dj song 4B <歌曲>         只看 4B\n"
        "  dj info 4B <歌曲>         查你在该曲 4B 的成绩\n"
        "  歌曲可以是曲名、歌手或 songid；未绑定时写 dj <ID> info <歌曲>\n"
        "提示：已绑定的 ID 可省略，例如 dj b100 4"
    )


# --- 消息解析 -----------------------------------------------------------------


def parse_invocation(text: str) -> list[str] | None:
    """把消息正文解析成参数列表；不是 DJMAX 命令时返回 None。"""
    text = unicodedata.normalize("NFKC", text or "")
    text = _INVISIBLE.sub(" ", text).strip().lstrip("/／").strip()
    if not text:
        return None
    head, _, tail = text.partition(" ")
    if head.lower() not in NAMESPACES:
        return None
    return tail.split() if tail.strip() else []


def _plain(event: MessageEvent) -> str:
    try:
        return event.get_message().extract_plain_text()
    except Exception:  # pragma: no cover - 事件结构异常
        return ""


def _allowed(event: MessageEvent) -> bool:
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

    if isinstance(event, PrivateMessageEvent):
        return config.djmax_private
    if isinstance(event, GroupMessageEvent):
        allow = {int(g) for g in config.djmax_group_ids}
        return not allow or event.group_id in allow
    return False


def _rule(event: MessageEvent) -> bool:
    return _allowed(event) and parse_invocation(_plain(event)) is not None


djmax_matcher = on_message(rule=Rule(_rule), priority=8, block=True)


def _split_bmode_token(token: str) -> int | None:
    cleaned = token.upper().rstrip("B")
    if cleaned in {"4", "5", "6", "8"}:
        return int(cleaned)
    return None


def _match_diff(token: str) -> str | None:
    for diff in DIFFS:
        if token.upper().startswith(diff):
            return diff
    return None


def _parse_level_token(tokens: list[str]) -> tuple[bool, int] | None:
    """解析 ``list`` 的等级参数，支持 ``15`` / ``sc15`` / ``SC 15``。"""
    if not tokens:
        return None
    text = " ".join(tokens).strip()
    match = _LEVEL_RE.match(text)
    if not match:
        return None
    return bool(match.group(1)), int(match.group(2))


# --- 数据 ---------------------------------------------------------------------


async def _song_index() -> tuple[song_index.SongIndex, dict[str, str]]:
    global _index, _dlc_names
    if _index is None:
        db = await api_handler.fetch_song_db()
        _index = song_index.SongIndex(db)
    if _dlc_names is None:
        try:
            _dlc_names = await single_query.fetch_dlc_names()
        except Exception:
            _dlc_names = {}
    return _index, _dlc_names


def _songid_hint(token: str | None) -> str | None:
    """``441`` / ``#441`` 这类纯数字参数视为 songid 提示；连带键数的整串也认。"""
    text = (token or "").strip().lstrip("#＃")
    return text if text.isdigit() else None


async def _solve_song(query: str,
                      hint: str | None = None) -> tuple[DMSongDBEntry | None, list[song_index.SongHit]]:
    """把用户输入解析成唯一曲目；无法确定时返回全部候选。

    真正的判定规则在 ``song_index.resolve``：songid / 全等曲名 / 全等歌手优先，
    只有前缀、子串这类模糊命中才看分差，并返回带优先级的候选列表。
    ``hint`` 是用户顺手写的 songid（例如 ``dj info 4B 441``），用于同名消歧。
    """
    index, _ = await _song_index()
    # 候选列表里显示成 "#441"，用户常连 # 一起抄回来，归一化时去掉。
    query = query.strip().lstrip("#＃").strip()
    result = song_index.resolve(index, query, hint=hint)
    if result.entry is not None:
        return result.entry, list(result.hits)
    if len(result.hits) == 1:
        # 前缀 / 歌手子串只命中一首时就是它，不必为难用户。
        return result.hits[0].entry, list(result.hits)
    return None, list(result.hits) or tuple(index.search(query))


def _candidate_text(query: str, hits: list[song_index.SongHit]) -> str:
    # 候选里带 # 只是为了醒目；示意命令里一律用不带 # 的纯数字 songid。
    first = hits[0].entry
    sample = " / ".join(str(h.entry.songid) for h in hits[:2])
    bmode_hint = "4B " if first.patterns.for_bmode(4) else ""
    lines = [
        f"「{query}」匹配到 {len(hits)} 首，无法确定是哪一首。",
        f"请换用完整曲名，或把 songid 写在命令里（例如 songid {sample}，"
        f"如 dj info {bmode_hint}{first.songid}）：",
    ]
    for hit in hits[:8]:
        entry = hit.entry
        lines.append(f"  #{entry.songid}  {entry.title} — {entry.composer or '—'}")
    if len(hits) > 8:
        lines.append(f"  …… 还有 {len(hits) - 8} 个")
    return "\n".join(lines)


async def _resolve_id(user_id: str, args: list[str]) -> tuple[str | None, int, str | None]:
    if args and not args[0].lower() in QUERY_KEYWORDS and args[0].isdigit():
        return args[0], 1, None
    bound = bind_store.get_bound(user_id)
    if not bound:
        return None, 0, "还没绑定 V-Archive ID。先发送 dj bind <ID>，或查询时在操作前填 ID。"
    return bound, 0, None


QUERY_KEYWORDS = {
    CMD_BESTS, CMD_LIST, CMD_PACK, CMD_NEW, CMD_PP, CMD_MAX,
    *_ALIAS_SONG, *_ALIAS_SCORE,
}


# --- 出图 ---------------------------------------------------------------------


async def _send_image(matcher_or_bot, event: MessageEvent, image) -> None:
    png = render.to_png(image)
    await djmax_matcher.send(MessageSegment.image(png), at_sender=False)


# --- 命令实现 -----------------------------------------------------------------


async def do_song_card(event: MessageEvent, query: str, bmode: int | None,
                       hint: str | None = None) -> None:
    entry, hits = await _solve_song(query, hint=hint)
    if entry is None:
        if hits:
            await djmax_matcher.send(_candidate_text(query, hits))
        else:
            await djmax_matcher.send(f"没找到「{query}」。可以试试曲名的一部分，或直接写 songid。")
        return
    _, dlc_names = await _song_index()
    info = single_query.build_song_info(entry, dlc_names, bmode=bmode)
    try:
        image = await render.render_song_info(info)
    except Exception as exc:  # pragma: no cover - 渲染兜底
        await djmax_matcher.send(f"出图失败：{exc}。曲目信息：{entry.title} — {entry.composer or '—'}")
        return
    await djmax_matcher.send(MessageSegment.image(render.to_png(image)))


async def do_score_card(event: MessageEvent, username: str, query: str, bmode: int,
                        hint: str | None = None) -> None:
    entry, hits = await _solve_song(query, hint=hint)
    if entry is None:
        if hits:
            await djmax_matcher.send(_candidate_text(query, hits))
        else:
            await djmax_matcher.send(f"没找到「{query}」。可以试试曲名的一部分，或直接写 songid。")
        return
    _, dlc_names = await _song_index()
    try:
        scores = await single_query.fetch_song_scores(
            username, entry, bmode=bmode, dlc_names=dlc_names
        )
    except Exception as exc:
        await djmax_matcher.send(f"查询成绩失败：{exc}")
        return
    if not scores.has_any:
        names = "/".join(d for d in DIFFS if any(c.diff == d for c in scores.song.charts))
        await djmax_matcher.send(
            f"{username} 在 {bmode}B「{entry.title}」还没有成绩（{names}）。"
        )
        return
    try:
        image = await render.render_song_scores(scores)
    except Exception as exc:  # pragma: no cover - 渲染兜底
        best = "\n".join(
            f"  {s.diff} {s.constant:g} {s.score}% {s.state}".rstrip()
            for s in scores.scores
            if s.score is not None
        )
        await djmax_matcher.send(f"出图失败：{exc}\n{entry.title} {bmode}B\n{best}")
        return
    await djmax_matcher.send(MessageSegment.image(render.to_png(image)))


async def _handle_single(event: MessageEvent, user_id: str, rest: list[str], *, is_score: bool,
                         forced_id: str | None = None) -> None:
    """``dj [ID] info|song [4B] <歌曲>``"""
    if not rest:
        await djmax_matcher.send(
            "用法：dj song <歌曲> 或 dj song 4B <歌曲>；查自己成绩用 dj info 4B <歌曲>"
        )
        return

    bmode: int | None = None
    tokens = list(rest)
    # 纯数字参数既可能是键数（4/5/6/8），也可能是 songid。
    # 先认键数，剩下的部分才是曲名 / songid。
    if (first := _split_bmode_token(tokens[0])) is not None:
        bmode = first
        tokens = tokens[1:]
    elif len(tokens) > 1 and (last := _split_bmode_token(tokens[-1])) is not None:
        bmode = last
        tokens = tokens[:-1]

    query = " ".join(tokens).strip().lstrip("#＃").strip()
    # "dj info 4B 441"：键数之后紧跟的那个数字就是 songid，同名曲靠它消歧。
    hint = _songid_hint(" ".join(tokens)) if bmode is not None else None
    if not query and is_score and bmode in (None, 4) and hint is not None:
        # "dj info 441"：songid 被写在了键数位置。这里没有曲名可查，再回一句
        # "缺少键数"只会让人重发一次，直接按 4B 查该曲成绩更实用。
        query, bmode = hint, 4
    if not query:
        await djmax_matcher.send("缺少歌曲名。例如：dj song Krush")
        return

    if not is_score:
        await do_song_card(event, query, bmode, hint=hint)
        return

    if bmode is None:
        await djmax_matcher.send("查单曲成绩需要指定键数，例如：dj info 4B Krush")
        return

    username = forced_id
    if username is None:
        username, _, err = await _resolve_id(user_id, [])
        if err or not username:
            await djmax_matcher.send(
                err or "还没绑定 V-Archive ID。先发送 dj bind <ID>，或改用 dj <ID> song 4B <歌曲>。"
            )
            return
    await do_score_card(event, username, query, bmode, hint=hint)


@djmax_matcher.handle()
async def _(bot: Bot, event: MessageEvent) -> None:
    # 兜底：任何未预期异常都要给用户一个可读回复，绝不能静默吞掉。
    try:
        await _run_command(event)
    except Exception as exc:
        try:
            await djmax_matcher.send(f"DJMAX 查询出错：{type(exc).__name__}: {exc}")
        except Exception:
            pass


async def _run_command(event: MessageEvent) -> None:
    user_id = str(event.get_user_id())
    args = parse_invocation(_plain(event))
    if args is None:  # rule 已经保证，这里只做防御

        return

    if not args:
        await djmax_matcher.send(build_help_text())
        return

    first = args[0].lower()
    if first == CMD_BIND:
        if len(args) < 2:
            await djmax_matcher.send("请填 V-Archive ID，例如：dj bind <ID>")
            return
        va_id = args[1]
        bind_store.bind(user_id, va_id)
        await djmax_matcher.send(f"已绑定 V-Archive ID：{va_id}")
        return

    if first == CMD_UNBIND:
        removed = bind_store.unbind(user_id)
        await djmax_matcher.send("已解绑。" if removed else "当前没有绑定。")
        return

    if first in _ALIAS_HELP:
        await djmax_matcher.send(build_help_text())
        return

    if first == CMD_LISTDLC:
        try:
            names = await single_query.fetch_dlc_names()
        except Exception as exc:
            await djmax_matcher.send(f"获取曲包列表失败：{exc}")
            return
        lines = ["曲包代码（可直接用于 dj pack）："]
        lines.extend(f"  {code:<6} {name}" for code, name in sorted(names.items()))
        await djmax_matcher.send("\n".join(lines))
        return

    # 单曲命令：dj [ID] info|song [4B] <歌曲>
    if first in _ALIAS_SONG or first in _ALIAS_SCORE:
        is_score = first in _ALIAS_SCORE
        await _handle_single(event, user_id, args[1:], is_score=is_score)
        return

    username, start_idx, err = await _resolve_id(user_id, args)
    if err:
        await djmax_matcher.send(err)
        return
    assert username is not None

    if start_idx >= len(args):
        await djmax_matcher.send(
            "参数还不够。B100 例子：dj b100 4；分表例子：dj list 4 15；"
            "查单曲例子：dj song 441 或 dj info 4B 441。"
        )
        return

    keyword = args[start_idx].lower()
    if keyword in _ALIAS_SONG or keyword in _ALIAS_SCORE:
        await _handle_single(
            event, user_id, args[start_idx + 1 :],
            is_score=keyword in _ALIAS_SCORE, forced_id=username,
        )
        return

    next_idx = start_idx + 1
    if next_idx >= len(args):
        await djmax_matcher.send("缺少谱面键数(4/5/6/8)。")
        return
    try:
        keys = int(args[next_idx])
        if keys not in VALID_BMODES:
            raise ValueError
        next_idx += 1
    except ValueError:
        await djmax_matcher.send("谱面键数必须是有效的数字 (4/5/6/8)。")
        return

    try:
        if keyword == CMD_BESTS:
            image = await api_handler.fetch_bests(username, keys)
            from .djmax_bests import generate

            await djmax_matcher.send(MessageSegment.image(render.to_png(await generate.generate_bests(username, keys))))
        elif keyword == CMD_MAX:
            from .djmax_bests import generate

            await djmax_matcher.send(
                MessageSegment.image(render.to_png(await generate.generate_bests_theoretical(keys)))
            )
        elif keyword == CMD_LIST:
            parsed = _parse_level_token(args[next_idx:])
            if parsed is None:
                await djmax_matcher.send("分表等级格式错误。请使用类似 15、sc15、SC 15 的格式。")
                return
            is_sc, level = parsed
            from .djmax_bests import generate

            await djmax_matcher.send(
                MessageSegment.image(
                    render.to_png(await generate.generate_scorelist(username, keys, is_sc, level))
                )
            )
        elif keyword == CMD_PACK:
            remaining = args[next_idx:]
            if len(remaining) < 2:
                await djmax_matcher.send(
                    "曲包查询缺少参数，例如：dj pack 4 SC Platinum。\n"
                    "难度支持 NM、HD、MX、SC；曲包代码用 dj listdlc 查询。"
                )
                return
            diff = _match_diff(remaining[0])
            pack = " ".join(remaining[1:]).strip()
            if diff is None:
                await djmax_matcher.send("难度格式错误。请使用 SC、NM、HD 或 MX 开头。")
                return
            if not pack:
                await djmax_matcher.send("未检测到有效的曲包代码（dlcCode）。")
                return
            from .djmax_bests import generate

            await djmax_matcher.send(
                MessageSegment.image(
                    render.to_png(await generate.generate_scorelist_pack(username, keys, diff, pack.upper()))
                )
            )
        elif keyword == CMD_NEW:
            remaining = args[next_idx:]
            if not remaining:
                await djmax_matcher.send("新曲分表缺少难度，例如：dj new 4 MX")
                return
            diff = _match_diff(remaining[0])
            if diff is None:
                await djmax_matcher.send("难度格式错误。请使用 SC、NM、HD 或 MX。")
                return
            from .djmax_bests import generate

            await djmax_matcher.send(
                MessageSegment.image(
                    render.to_png(await generate.generate_scorelist_new(username, keys, diff))
                )
            )
        elif keyword == CMD_PP:
            from .djmax_bests import generate

            await djmax_matcher.send(
                MessageSegment.image(
                    render.to_png(await generate.generate_scorelist_pp(username, keys))
                )
            )
        else:
            await djmax_matcher.send(
                f"未知的指令关键词 `{keyword}`。目前支持: b100, list, new, pack, pp, max, info, song"
            )
    except Exception as exc:
        await djmax_matcher.send(f"查询失败：{exc}")

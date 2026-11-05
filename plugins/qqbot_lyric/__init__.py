"""歌词接龙插件（qqbot_lyric）。

群里有人发了一句歌词，机器人接着唱下一句 —— 像个记得歌词的群友。

- **自然触发**：群友随手打一句「从前从前有个人爱你很久」，bot 认出是《晴天》，
  就接「但故事的最后你好像还是说了拜拜」；@机器人 或引用某条消息时同样会响应；
- **连续接**：接过之后 2 分钟内，别人再发同首歌的某一句，bot 继续往下接，
  不会突然换歌；
- **查歌词**：``/歌词 <歌名>``、``/接 <关键词>`` 直接给出歌名歌手、命中行与下一句；
- **按群开关**：默认关闭，群管发 ``/开启 歌词`` 打开（``/本群功能`` 查看）。

判定分两层：先按「歌词长什么样」做本地版式闸门（长度/分行/押韵/口语词），
再把拿不准的交给模型确认；确认是歌词后，再用曲库（lrclib 为主，网易云搜索兜底）
定位到具体某首歌的某一行，接出下一句。
"""

import asyncio
import json
import time
from typing import Optional

import nonebot
from nonebot import get_driver, get_plugin_config, on_command, on_message, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

require("nonebot_plugin_localstore")

from nonebot_plugin_localstore import get_plugin_data_dir  # noqa: E402

from . import features  # noqa: E402
from .config import Config, plugin_config  # noqa: E402
from .judge import LLMJudge  # noqa: E402
from .library import Library  # noqa: E402
from .relay import RelayEngine, render, render_title_only  # noqa: E402
from .search import SearchContext, search  # noqa: E402
from .state import RelayStore  # noqa: E402
from .store import IndexStore  # noqa: E402
from .text import clean_text, is_noise  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="歌词接龙",
    description="群友发歌词，机器人接下一句（本地版式判断 + 模型确认 + lrclib/网易云曲库）",
    usage="""\
（无需命令：群里发一句歌词，bot 认出歌名后自动接下一句；
  @机器人 发歌词、或引用一句歌词再 @机器人，也会接）
/歌词 <歌名/歌词片段>   查歌词：歌名歌手 + 命中行 + 下一句
/接 <关键词>            同 /歌词
/歌词 完整版 <歌名>     输出整首歌词（最多 40 行）
/歌词 <歌名> <歌词片段>  按歌名找歌，再定位片段（歌名不确定时用）
/本群功能               开关「歌词接龙」（默认关闭，群管用 /开启 歌词 打开）""",
    type="application",
    homepage="https://github.com/EricScottLin/Erechtheus",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

DATA_DIR = get_plugin_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _ai_value(name: str) -> str:
    """取 qqbot_ai 的同名配置（插件未加载或没配就用空串）。"""
    try:
        return str(getattr(get_driver().config, name, "") or "")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 组件装配
library = Library(
    lrclib_base=plugin_config.qqbot_lyric_lrclib_base,
    timeout=plugin_config.qqbot_lyric_timeout_seconds,
    max_song_lines=plugin_config.qqbot_lyric_max_song_lines,
    cache_songs=plugin_config.qqbot_lyric_cache_songs,
    cache_line_index=plugin_config.qqbot_lyric_cache_line_index,
    netease_enabled=plugin_config.qqbot_lyric_netease_fallback,
)

index_store = IndexStore(DATA_DIR / "library.json")
index_store.load_into(library.index)

judge = LLMJudge(
    api_base=plugin_config.qqbot_lyric_llm_api_base or _ai_value("qqbot_ai_api_base"),
    api_key=plugin_config.qqbot_lyric_llm_api_key or _ai_value("qqbot_ai_api_key"),
    model=plugin_config.qqbot_lyric_llm_model or _ai_value("qqbot_ai_model"),
    timeout=plugin_config.qqbot_lyric_llm_timeout_seconds,
    max_tokens=plugin_config.qqbot_lyric_llm_max_tokens,
    batch_size=plugin_config.qqbot_lyric_llm_batch_size,
    batch_wait=plugin_config.qqbot_lyric_llm_batch_wait,
    disabled=not plugin_config.qqbot_lyric_use_llm,
)

store = RelayStore()
cfg = plugin_config.model_copy(deep=True)
engine = RelayEngine(cfg, library, judge, store)

# 每群一个串行锁：同一群里两条消息同时处理会互相打乱「连续接」状态
_group_locks: dict[int, asyncio.Lock] = {}


def _lock_for(group_id: int) -> asyncio.Lock:
    lock = _group_locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _group_locks[group_id] = lock
    return lock


# ---------------------------------------------------------------- 上下文重置
def reset_group(group_id: int) -> None:
    """清空某群的全部接龙上下文（测试与 /歌词 重置用）。"""
    engine.reset_group(group_id)


# ---------------------------------------------------------------- 权限 / 开关
ADMIN_ROLES = frozenset({"owner", "admin"})


def _is_admin(bot: Bot, event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        if getattr(event.sender, "role", None) in ADMIN_ROLES:
            return True
    uid = event.get_user_id()
    try:
        superusers = {str(x) for x in getattr(getattr(bot, "config", None), "superusers", set())}
    except Exception:  # noqa: BLE001
        superusers = set()
    return uid in superusers


def _enabled(group_id: int) -> bool:
    return plugin_config.qqbot_lyric_enabled and features.is_enabled(group_id)


# ---------------------------------------------------------------- 自然触发
async def _relay_rule(event: GroupMessageEvent) -> bool:
    """只处理：本群已开启 + 纯文本 + 不像命令 + 不是噪音。"""
    if not _enabled(event.group_id):
        return False
    if not _text_of(event):
        return False
    return True


def _text_of(event: MessageEvent) -> str:
    """取消息里的文字（去掉 @ 段、回复段），非纯文本返回空串。"""
    segs = [s for s in event.get_message() if s.type not in ("at", "reply", "face")]
    if not segs:
        return ""
    if any(s.type != "text" for s in segs):
        return ""
    raw = "".join(str(s.data.get("text") or "") for s in segs).strip()
    if is_noise(raw):
        return ""
    return clean_text(raw)


async def _quoted_text(bot: Bot, event: MessageEvent) -> str:
    """被引用消息的文字（引用消息常用来「点名某句歌词」）。"""
    reply = getattr(event, "reply", None)
    if reply is None:
        return ""
    try:
        message = reply.message
    except Exception:  # noqa: BLE001
        message = None
    if message is None:
        return ""
    segs = [s for s in message if s.type not in ("at", "reply", "face")]
    if not segs or any(s.type != "text" for s in segs):
        return ""
    return clean_text("".join(str(s.data.get("text") or "") for s in segs))


relay = on_message(rule=Rule(_relay_rule), priority=60, block=False)


@relay.handle()
async def _handle_relay(bot: Bot, event: GroupMessageEvent) -> None:
    text = _text_of(event)
    if not text:
        return
    explicit = event.is_tome()
    quoted = await _quoted_text(bot, event) if (explicit or event.reply is not None) else ""
    if quoted:
        text = quoted
        explicit = True
    # 命令（/xxx）不参与接龙
    if text.startswith(("/", "／")):
        return

    lock = _lock_for(event.group_id)
    if lock.locked():
        return  # 同群上一条还在处理，跳过这条，避免排队刷屏
    async with lock:
        try:
            outcome = await asyncio.wait_for(
                engine.handle(
                    event.group_id,
                    text,
                    event.get_user_id(),
                    ts=time.monotonic(),
                    explicit=explicit,
                    recent_fragments=await _recent_texts(bot, event) if explicit else None,
                ),
                timeout=45,
            )
        except asyncio.TimeoutError:
            logger.debug("[qqbot_lyric] 处理超时，跳过")
            return
        except Exception as e:  # noqa: BLE001 —— 接歌词失败绝不能影响群里其他功能
            logger.warning(f"[qqbot_lyric] 处理消息异常：{type(e).__name__} {e}")
            return
    logger.debug(
        f"[qqbot_lyric] 群 {event.group_id} 结果 {outcome.status}"
        f"（{outcome.elapsed:.2f}s，{outcome.reason}）"
    )
    if not outcome.replied or outcome.hit is None:
        return
    if not plugin_config.qqbot_lyric_auto_reply:
        return
    reply = render(outcome.hit, plugin_config.qqbot_lyric_reply_mode)
    if reply:
        await relay.finish(MessageSegment.reply(event.message_id) + MessageSegment.text(reply))


async def _recent_texts(bot: Bot, event: GroupMessageEvent) -> list[tuple[str, str, float]]:
    """@机器人 发言时，从群里取回最近几条消息（最多 12 条，20 分钟内）。"""
    try:
        history = await bot.call_api(
            "get_group_msg_history",
            group_id=event.group_id,
            count=12,
        )
    except Exception as e:  # noqa: BLE001 —— 拿不到历史就只按当前这条判
        logger.debug(f"[qqbot_lyric] 读取群历史失败：{e}")
        return []
    out: list[tuple[str, str, float]] = []
    for msg in (history or {}).get("messages") or []:
        sender = (msg.get("sender") or {})
        uid = str(msg.get("user_id") or sender.get("user_id") or "")
        if uid == str(bot.self_id):
            continue
        message = msg.get("message")
        segs = [s for s in (message or []) if isinstance(s, dict) and s.get("type") == "text"]
        if not segs or len(segs) != len(message or []):
            continue
        text = clean_text("".join(str((s.get("data") or {}).get("text") or "") for s in segs))
        if not text or is_noise(text):
            continue
        try:
            ts = float(msg.get("time") or time.time())
        except (TypeError, ValueError):
            ts = time.time()
        out.append((text, uid, ts))
    return _relative_timestamps(out)


def _relative_timestamps(items: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    """把「墙钟秒」「单调时钟」两种时间基准对齐成单调时钟（测试可直接传单调秒）。"""
    now_mono = time.monotonic()
    now_wall = time.time()
    out = []
    for text, uid, ts in items:
        if ts > 3_000_000_000:  # 明显是 Unix 时间戳
            out.append((text, uid, now_mono - max(0.0, now_wall - ts)))
        else:
            out.append((text, uid, ts))
    return out


# ---------------------------------------------------------------- /歌词 命令
lyric_cmd = on_command("歌词", aliases={"接", "查歌词", "下一句"}, priority=8, block=True)


@lyric_cmd.handle()
async def _handle_lyric(bot: Bot, event: MessageEvent, arg: Message = CommandArg()) -> None:
    if isinstance(event, GroupMessageEvent) and not _enabled(event.group_id):
        await lyric_cmd.finish("本群没开歌词接龙，找群管发「/开启 歌词」。")
    raw = arg.extract_plain_text().strip()
    if not raw:
        await lyric_cmd.finish(
            "用法：\n"
            "/歌词 <歌名或歌词片段>   查歌词（歌名歌手 + 下一句）\n"
            "/歌词 完整版 <歌名>      输出整首歌词\n"
            "群里直接发歌词，bot 也会自动接下一句～"
        )

    full = False
    if raw.startswith(("完整版", "全文", "全部")):
        full = True
        raw = raw[3:].strip()
    if not raw:
        await lyric_cmd.finish("要查哪首歌？例如 /歌词 晴天")

    try:
        reply = await asyncio.wait_for(_query(raw, full), timeout=30)
    except asyncio.TimeoutError:
        reply = "查得有点久（可能曲库没响应），换个说法再试？"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_lyric] /歌词 失败：{type(e).__name__} {e}")
        reply = "刚才查歌词出错了，稍后再试。"
    await lyric_cmd.finish(reply)


def _split_title_and_fragment(raw: str) -> tuple[str, str]:
    """``「晴天 从前从前有个人爱你很久」`` → ``("晴天", "从前从前有个人爱你很久")``。

    规则：第一段是书名号里的 / 很短的词，且后面还有内容 → 当成「歌名 + 片段」。
    """
    from .text import clean_text

    parts = raw.split(maxsplit=1)
    if len(parts) == 2 and len(parts[0]) <= 20 and parts[1].strip():
        return clean_text(parts[0]), clean_text(parts[1])
    return "", ""


async def _query(raw: str, full: bool = False) -> str:
    """查歌词：先当歌名找，再当片段定位。"""
    from .library import clean_title
    from .text import Fragment, normalize

    title_hint, fragment = _split_title_and_fragment(raw)

    # 0) 「歌名 + 片段」：用标题找歌，再用片段定位
    if title_hint and fragment:
        song = await library.find_by_name(title_hint)
        if song is not None:
            frag = Fragment(text=fragment, norm=normalize(fragment), user_id="0", ts=time.monotonic())
            ctx = SearchContext.build([frag], explicit_title=clean_title(song.title), recent_song=song)
            hit = await search(library, ctx, min_score=plugin_config.qqbot_lyric_min_score)
            if hit is not None:
                return _render_hit(hit)
            lines = library.index.lines_of(song)
            return "\n".join([f"🎵 《{song.title}》- {song.artist}", *lines[:5], "…"])

    # 1) 当歌名
    song = await library.find_by_name(raw)
    if song is not None:
        lines = library.index.lines_of(song)
        out = [f"🎵 《{song.title}》- {song.artist}" if song.artist else f"🎵 《{song.title}》"]
        if full:
            out.extend(line for line in lines[:40])
            if len(lines) > 40:
                out.append(f"…（共 {len(lines)} 行）")
        else:
            out.append("歌词：" + lines[0] if lines else "")
            if len(lines) > 1:
                out.append("（用 /歌词 完整版 " + song.title + " 看整首）")
        return "\n".join(line for line in out if line)

    # 2) 当片段
    frag = Fragment(text=raw, norm=normalize(raw), user_id="0", ts=time.monotonic())
    ctx = SearchContext.build([frag], explicit_title=clean_title(raw) if len(raw) <= 12 else "")
    hit = await search(library, ctx, min_score=plugin_config.qqbot_lyric_min_score)
    if hit is None:
        return f"没找到和「{raw}」对应的歌，可能是我曲库里没有（试试 /歌词 <歌名>）。"
    return _render_hit(hit)


def _render_hit(hit) -> str:
    song = hit.song
    out = [f"🎵 《{song.title}》- {song.artist}" if song.artist else f"🎵 《{song.title}》"]
    for idx in range(max(0, hit.start - 1), min(hit.end + 1, len(hit.lines))):
        out.append(hit.lines[idx])
    nxt = hit.next_lines
    if nxt:
        out.append("下一句：" + nxt[0])
    out.append(f"（命中第 {hit.start + 1} 行，相似度 {hit.score:.2f}）")
    return "\n".join(out)


# ---------------------------------------------------------------- 生命周期
_driver = get_driver()


def _save_index() -> None:
    try:
        index_store.save_from(library.index)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_lyric] 保存歌词索引失败：{e}")


@_driver.on_startup
async def _startup() -> None:
    if not plugin_config.qqbot_lyric_enabled:
        logger.info("[qqbot_lyric] 插件总开关已关闭（qqbot_lyric_enabled=false）")
    logger.info(
        f"[qqbot_lyric] 配置快照：{json.dumps(plugin_config.model_dump(mode='json'), ensure_ascii=False)}"
    )
    logger.info(
        f"[qqbot_lyric] 就绪：lrclib={plugin_config.qqbot_lyric_lrclib_base} "
        f"netease={plugin_config.qqbot_lyric_netease_fallback} "
        f"llm={'on:' + judge.model if (judge.configured and plugin_config.qqbot_lyric_use_llm) else 'off'} "
        f"索引歌曲={library.index.song_count}"
    )


@_driver.on_shutdown
async def _shutdown() -> None:
    _save_index()
    await library.close()
    await judge.close()


# ---------------------------------------------------------------- 定时任务
try:
    from nonebot_plugin_apscheduler import scheduler

    @scheduler.scheduled_job("interval", minutes=5, id="qqbot_lyric_cleanup")
    async def _cleanup() -> None:
        engine.cleanup(time.monotonic())

    @scheduler.scheduled_job("interval", minutes=10, id="qqbot_lyric_save")
    async def _periodic_save() -> None:
        if library.index.song_count:
            _save_index()
except Exception as e:  # noqa: BLE001 —— 没有 apscheduler 也能工作，只是不自动落盘
    logger.debug(f"[qqbot_lyric] 定时任务未启用：{e}")

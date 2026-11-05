"""待办清单插件（qqbot_todo）。

**仅超级用户可用**（默认私聊；群里用需 ``qqbot_todo_group_enabled=true``）。

自然语言录入::

    我要做 给群管插件加个冷却
    !!我要做 修复 /烟 的冷却 #群管      # !! = 高优先级，! = 紧急，~ = 低
    我 要做 写周报 #周报               # QQ 输入法会把「我要做」拆成两条

管理（指令 ``/待办``，别名 ``/todo``）::

    /待办                     查看清单
    /待办 列表 [完成|全部]      按优先级查看 / 看已完成 / 全部
    /待办 做 <任务>             另一种新增写法
    /待办 完成 3    我做完了 3   按编号完成（也认关键词，唯一命中才执行）
    /待办 放弃 3              放弃（保留记录）
    /待办 删除 3              只删已完成/已放弃的
    /待办 开始 3              标记进行中
    /待办 改 3 <新标题>   /待办 优先级 3 高   /待办 备注 3 ...   /待办 标签 3 #a #b
    /待办 提醒 3 明天 9 点     到点私聊提醒
    /待办 记录 3 <DSH任务ID>   回填 DSH 任务 ID
    /待办 交接 <标题>          给交接文件起名（会话内设置）
    /待办 导出 [json]          生成交接文件

联动 DSH::

    /待办 导出  →  按 qqbot_todo_dsh_dir 写 todo_handoff.md，并把全文私聊给超管
    之后在 DSH 里说「读 <那个文件>，在 <工作区> 里开始做」即可
    安排完之后用 /待办 记录 <编号> <DSH 任务 ID> 回填，便于对账

遵循 NoneBot 官方插件编写规范：
https://nonebot.dev/docs/tutorial/create-plugin
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import nonebot
from nonebot import get_plugin_config, on_command, on_message, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from . import features, store
from .action_parse import Action, parse_command
from .config import Config, split_hhmm
from .helpers import (
    MAIN_ALIASES,
    PRIORITY_LABELS,
    Action,
    export_json,
    export_markdown,
    fmt_relative,
    is_fresh,
    line,
    list_text,
    normalize_spaces,
    parse_id,
    parse_quick,
    parse_when,
    summarize,
)

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="待办清单",
    description="超级用户的 TODO：自然语言录入、按优先级查看、完成/放弃、到点提醒，可交接给 DSH 直接安排",
    usage="""\
我要做 <任务>                 新增待办（!紧急 !!高 ~低 #标签）
/待办                        查看清单
/待办 完成 <编号|关键词>       完成（也可发「我做完了 3」）
/待办 放弃 <编号>  /待办 删除 <编号>
/待办 优先级 <编号> <紧急|高|中|低>
/待办 备注 / 标签 / 改 <编号> ...
/待办 提醒 <编号> <时间>      18:00 / 明天 9 点 / 3天后
/待办 导出 [json]             生成交接文件（配合 /待办 交接 <标题>）""",
    type="application",
    homepage="https://github.com/nonebot-qqbot",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)

HELP = """📝 待办清单（仅超级用户）
【新增】我要做 <任务>    也认拆开发：我 / 要做 <任务>
  !紧急  !!高  ~低（不写默认「中」）  #标签
  例：!!我要做 修复 /烟 的冷却 #群管
【查看】/待办    /待办 列表 完成    /待办 列表 全部
【完成】/待办 完成 3    或 我做完了 3
【放下】/待办 放弃 3    /待办 删除 3
【整理】/待办 改 3 新标题   /待办 优先级 3 高   /待办 备注 3 …   /待办 标签 3 #x
【提醒】/待办 提醒 3 明天 9 点   （18:00 / 明天 9 点 / 3天后 / 2小时后）
【交给 DSH】/待办 交接 <标题>  然后 /待办 导出
发 /待办 交接 可查看当前交接标题。"""

# 会话内的交接标题：按「用户 + 会话」隔离
_handoff_titles: dict[tuple[str, str], str] = {}


def handoff_title_for(event: MessageEvent) -> str:
    return _handoff_titles.get((str(event.get_user_id()), event.get_session_id()), "待办交接")


# ---------------------------------------------------------------- 权限 / 会话
def _superusers() -> set[str]:
    return {str(uid) for uid in (nonebot.get_driver().config.superusers or set())}


def _is_superuser(uid: Any) -> bool:
    return str(uid) in _superusers()


def _in_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


def _session_ok(event: MessageEvent) -> bool:
    """私聊永远可用；群里要「总闸开着 + 本群开关开着」。

    本群开关就是 qqbot_group 的那套：``/开启 待办``、``/关闭 待办``、``/本群功能``。
    """
    if _in_private(event):
        return True
    if not plugin_config.qqbot_todo_group_enabled:
        return False
    return features.is_enabled(event.group_id)


def _session_denial(event: MessageEvent) -> Optional[str]:
    if not _is_superuser(event.get_user_id()):
        return "待办功能只开放给机器人超级用户。"
    if _in_private(event):
        return None
    if not plugin_config.qqbot_todo_group_enabled:
        return "待办在群里的总开关关着（qqbot_todo_group_enabled=false），先私聊用，或让运维打开。"
    if not features.is_enabled(event.group_id):
        return "本群还没开启待办：发 /开启 待办（管理员），或在 /本群功能 里打开。"
    return None


def _has_explicit_prefix(event: Any) -> bool:
    """「@机器人」或以显式命令前缀开头。

    本项目 COMMAND_START 含 ""：on_command / on_message 会让裸消息也命中，
    裸词在群里太容易误触发（例如「我要做点作业」），所以再收一道。
    """
    if getattr(event, "to_me", False):
        return True
    starts = tuple(s for s in nonebot.get_driver().config.command_start if s)
    if not starts:
        return True
    text = _plain(event).lstrip()
    return text.startswith(starts)


def _lead_rule(event: MessageEvent) -> bool:
    return bool(_LEAD_RE.match(_lead_text(event)))


def _denied_lead(event: MessageEvent) -> bool:
    """非超管发来「/我要做 …」「/待办 …」：看一眼就拦下，不做任何事。"""
    return bool(_LEAD_RE.match(_lead_text(event))) or _has_explicit_prefix(event)


def _is_not_superuser(event: MessageEvent) -> bool:
    return not _is_superuser(event.get_user_id())


def _done_short_rule(event: MessageEvent) -> bool:
    text = _strip_command_start(_plain(event))
    return _match_done_word(text) is not None


def _superuser_rule() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        return _session_ok(event) and _is_superuser(event.get_user_id())

    return Rule(_checker)


def _source_of(event: MessageEvent) -> dict[str, Any]:
    if _in_private(event):
        return {"type": "private", "user_id": str(event.get_user_id())}
    return {
        "type": "group",
        "group_id": event.group_id,
        "user_id": str(event.get_user_id()),
    }


async def _pick_bot() -> Optional[Bot]:
    """提醒 / 推送用：随便挑一个在线的 OneBot 11 bot。"""
    for bot in nonebot.get_bots().values():
        if getattr(bot, "type", "") == "OneBot V11":
            return bot
    return None


async def _send_private(user_id: str, message: Message) -> bool:
    bot = await _pick_bot()
    if bot is None:
        logger.warning("[qqbot_todo] 没有在线的 OneBot 11 bot，私聊提醒发不出去")
        return False
    try:
        await bot.call_api("send_private_msg", user_id=int(user_id), message=message)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_todo] 私聊 {user_id} 失败：{e}")
        return False


# ---------------------------------------------------------------- 找任务
def _last_touched(task: dict[str, Any]) -> int:
    stamps = [int(task.get("created_at") or 0)]
    stamps += [int(h.get("at") or 0) for h in task.get("history") or []]
    return max(stamps)


def _resolve(event: MessageEvent, target: str) -> tuple[Optional[dict[str, Any]], str]:
    """把「3」「#3」「插件」「上次」解析成一条待办。返回 ``(任务, 错误文案)``。"""
    target = (target or "").strip()
    if target in ("上次", "最近", "last"):
        tasks = store.all_tasks()
        if not tasks:
            return None, "还没有任何待办。"
        return max(tasks, key=_last_touched), ""

    task_id = parse_id(target)
    if task_id is not None:
        task = store.get_task(task_id)
        if task is None:
            return None, f"没有 #{task_id} 这条待办。发 /待办 看看清单。"
        return task, ""
    if not target:
        return None, "没说要处理哪一条。发 /待办 看编号，再 /待办 完成 <编号>。"

    hits = store.find_task(target)
    if not hits:
        return None, f"没找到包含「{target}」的待办。"
    if len(hits) > 1:
        lines = "\n".join(f"· #{t['id']} {t['title']}" for t in hits[:8])
        return None, f"「{target}」匹配到 {len(hits)} 条，请用编号：\n{lines}"
    return hits[0], ""


# ---------------------------------------------------------------- 新增
async def _create(event: MessageEvent, parsed: dict[str, Any], raw: str):
    """新增一条待办并返回 (task, 回复文案)；超限时返回 (None, 提示)。"""
    if len(store.active_tasks()) >= plugin_config.qqbot_todo_max_active:
        return None, f"未完成的待办已经有 {plugin_config.qqbot_todo_max_active} 条了，先清一清吧。"
    title = _fix_known_words(parsed["title"])[: plugin_config.qqbot_todo_title_max]
    task = await store.add_task(
        title,
        priority=parsed["priority"],
        tags=parsed["tags"],
        by=str(event.get_user_id()),
        source={**_source_of(event), "raw": raw[:300]},
    )
    tags = "".join(f" #{t}" for t in task["tags"])
    prio = PRIORITY_LABELS[task["priority"]]
    hint = (
        f"（{prio}优先级）"
        if task["priority"] == 3
        else f"（{prio}优先级，可用「/待办 优先级 {task['id']} 中」改回）"
    )
    return task, f"✅ 记下了 #{task['id']} {task['title']}{tags}{hint}\n发 /待办 看清单；做完了发 我做完了 {task['id']}。"


_LEAD_RE = re.compile(r"^\s*(?P<marks>[!~]{0,3})\s*(?:我\s*)?要做(?P<rest>.*)$", re.S)


lead_todo = on_message(rule=_superuser_rule() & Rule(_lead_rule), priority=4, block=True)


# 非超管发「/我要做」「/待办」时，温柔提醒一次（优先级比超管的处理更高，先拦下）
denied_lead = on_message(rule=Rule(_denied_lead) & Rule(_is_not_superuser), priority=3, block=True)


@denied_lead.handle()
async def handle_denied_lead(event: MessageEvent) -> None:
    denial = _session_denial(event)
    if denial:
        await denied_lead.finish(denial)


async def _add_from_quick(event: MessageEvent, text: str, raw: str, matcher) -> None:
    """从「我要做 …」文本（含标记/#标签）新增，并直接 finish。"""
    parsed = parse_quick(text)
    if parsed is None:
        # 「我要做」后面空着：不吞消息，提示一下怎么用
        await matcher.finish("要做啥？发「我要做 <任务>」，例如：我要做 给群管插件加个冷却")
    task, message = await _create(event, parsed, raw)
    await matcher.finish(message)


def _plain(event: MessageEvent) -> str:
    """事件纯文本：直接拼接各 text 段的内容。

    不依赖 ``get_plaintext()`` 的拼接细节，拿到原文后由 ``_deglue`` /
    ``_fix_known_words`` 统一处理客户端插入的字间空格。
    """
    parts: list[str] = []
    for seg in event.get_message():
        if seg.type == "text":
            parts.append(str(seg.data.get("text") or ""))
    return "".join(parts)


def _strip_command_start(text: str) -> str:
    """去掉开头的命令前缀（``/`` 等），其余原样保留。"""
    starts = [s for s in nonebot.get_driver().config.command_start if s]
    stripped = (text or "").lstrip()
    for start in sorted(starts, key=len, reverse=True):
        if stripped.startswith(start):
            return stripped[len(start):]
    return stripped


def _lead_text(event: MessageEvent) -> str:
    """事件里「我要做 …」那段文本。

    本项目 COMMAND_START 含 ``""``，NoneBot 的 TrieRule 会把以 ``/`` 开头的
    未注册命令当成「空前缀 + 命令 + 参数」（``/我要做 修 bug`` -> 命令 ``我要做``，
    参数 ``修 bug``），所以显式前缀要把 命令 + 参数 拼回来。
    """
    prefix = _plain(event)
    try:
        parsed = event.get_metadata()
    except Exception:  # noqa: BLE001
        parsed = None
    _ = parsed
    return _strip_command_start(prefix)


@lead_todo.handle()
async def handle_lead(event: MessageEvent) -> None:
    text = _lead_text(event).strip()
    m = _LEAD_RE.match(text)
    if m is None:
        # 「/我要做 修 bug」：空前缀会把 我要做 当成命令词、修 bug 当成参数
        head, _, tail = text.partition(" ")
        joined = _strip_command_start(tail).strip()
        m = _LEAD_RE.match(joined)
        if m is None:
            return
        text = joined
    rest = m.group("rest").strip()
    # 裸「我要做点作业」多半是聊天，不记；带 / 前缀时一律记
    if not _has_explicit_prefix(event) and (not rest or re.match(r"^(点|些|个|一件|点儿)", rest)):
        return
    if _session_denial(event):
        return
    if not rest:
        await lead_todo.finish("要做啥？发「我要做 <任务>」，例如：我要做 给群管插件加个冷却")
    await _add_from_quick(event, f"{m.group('marks')}{rest}", text, lead_todo)


# ---------------------------------------------------------------- /待办
todo = on_command("待办", aliases={"todo", "TODO", "Todo"}, rule=_superuser_rule(), priority=4, block=True)


def _after_command(text: str, words: tuple[str, ...]) -> str:
    """从原文里剥掉「命令前缀 + 命令词」，剩下的就是参数。

    只在剥完命令之后合并字间空格：命令词和参数之间的空格是分隔符，不能合并。
    """
    text = (text or "").lstrip()
    starts = [s for s in nonebot.get_driver().config.command_start if s]
    for start in sorted(starts, key=len, reverse=True):
        if text.startswith(start):
            text = text[len(start):]
            break
    for word in sorted(words, key=len, reverse=True):
        if text.startswith(word):
            text = text[len(word):]
            break
    return _deglue(text.strip())


_KNOWN_WORDS = tuple(
    sorted({w for w in MAIN_ALIASES} | {"我要做", "做了没", "做完了", "不做了"}, key=len, reverse=True)
)
# 2 字及以上的命令词（单字词如 删/改/做 留给「1 字片段合并」处理）
_MULTI_WORDS = tuple(w for w in _KNOWN_WORDS if len(w) >= 2)


def _deglue(text: str) -> str:
    """把被客户端插空格的命令参数合回去。

    先从整体里认已知命令词（``完 成 周 报`` -> ``完成 周报``）；
    剩下的部分再把「每个字都是 1 字片段」的连续段合起来（``#群 管`` -> ``#群管``），
    遇到长度 >= 2 的片段（真正的字段分隔）就停。
    """
    text = (text or "").strip()
    # 1) 先认命令词：从头匹配「去掉空格后的前若干字」。
    #    只用 2 字及以上的词：单字词（删/改/做）在无空格输入里会把「删除」切成「删 除」。
    squashed = text.replace(" ", "")
    for word in _MULTI_WORDS:
        if squashed.startswith(word):
            ahead = len(word)
            consumed, pos = 0, 0
            while consumed < ahead and pos < len(text):
                if text[pos] != " ":
                    consumed += 1
                pos += 1
            text = text[pos:].strip()
            text = f"{word} {text}".strip()
            break

    # 已知词后面紧跟数字 / 中文时，补一个空格（「完成3」->「完成 3」）
    text = re.sub(rf"^({'|'.join(map(re.escape, _MULTI_WORDS))})(?=[0-9\u4e00-\u9fff])", r"\1 ", text)

    # 1.5) 命令词单独保留，不参与后面的「1 字片段合并」（否则「做 修 复」->「做修 复」）
    head, _, body = text.partition(" ")
    if head in _KNOWN_WORDS and body:
        return f"{head} {_merge_param_words(body)}"
    return _merge_param_words(text)


def _merge_param_words(text: str) -> str:
    """参数部分：1 字片段合并（``#群 管`` -> ``#群管``），支持交叉。"""
    parts = [p for p in text.split(" ") if p]

    def _merge1(parts: list[str]) -> list[str]:
        merged: list[str] = []
        for part in parts:
            if (
                merged
                and len(merged[-1]) == 1
                and len(part) == 1
                and not part.isdigit()
                and not merged[-1][-1].isdigit()
            ):
                merged[-1] += part
            else:
                merged.append(part)
        return merged

    parts = _merge1(parts)
    # 再合并「1 字 + 1 字」的相邻片段（如「结束 的 全部」里的「的 全」）

    def _merge_neighbors(parts: list[str]) -> list[str] | None:
        for i in range(len(parts) - 1):
            left, right = parts[i], parts[i + 1]
            if left and right and left[-1].isdigit() is False and right.isdigit() is False:
                if (len(left) == 1) != (len(right) == 1):
                    return parts[:i] + [left + right] + parts[i + 2:]
        return None

    for _ in range(8):
        changed = _merge_neighbors(parts)
        if changed is None:
            break
        parts = _merge1(changed)
    return " ".join(parts)


def _normalize_free_text(text: str) -> str:
    """备注这类自由文本彻底去字间空格：汉字之间、字母/数字两侧都合并。"""
    text = _fix_known_words(text)
    text = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[A-Za-z0-9])|(?<=[A-Za-z0-9]) +(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r" +(?=[，。；：、（）])|(?<=[，。；：、（]) +", "", text)
    return text


def _fix_known_words(text: str) -> str:
    """长文本（标题 / 备注 / 交接标题）：字段已拆好，合并掉字间空格。

    - 汉字之间直接连：``修 复 的 冷 却`` -> ``修复的冷却``
    - 数字夹在两个汉字之间：``冷 却 2 号`` -> ``冷却2号``
    - 空格紧挨 ASCII（``/烟``、英文单词）时保留一个
    """
    text = (text or "").strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff\uff08\uff09（）]) +(?=[\u4e00-\u9fff\uff08\uff09（）])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff\uff08\uff09（）]) +(?=[\uff00-\uffef])|(?<=[\uff00-\uffef]) +", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=\d)|(?<=\d) +(?=[\u4e00-\u9fff])", "", text)
    return normalize_spaces(text)


def _todo_arg(text: str) -> str:
    return _after_command(text, ("待办", "todo", "TODO", "Todo"))


@todo.handle()
async def handle_todo(event: MessageEvent) -> None:
    if not _has_explicit_prefix(event):
        return
    words = _todo_arg(_plain(event)).split()
    if words and words[0].lower() in ("todo",) and len(words) > 1:
        words = words[1:]  # 输入法把「/todo」换行写了，命令词跑到参数里
    action = parse_command(words[0] if words else "", words[1:])
    if action.error:
        await todo.finish(action.error)
    handler = _HANDLERS.get(action.name)
    if handler is None:
        await todo.finish(HELP)
    await handler(event, action)


async def _cmd_list(event: MessageEvent, action: Action) -> None:
    sub = (action.value or "").strip()
    if sub in ("完成", "已完成", "done"):
        tasks = [t for t in store.all_tasks() if t["status"] == "done"]
        await todo.finish(list_text(tasks, "✅ 已完成的待办", "还没有完成的待办。"))
    if sub in ("全部", "all", "历史"):
        tasks = store.all_tasks()
        body = list_text(tasks, "📚 全部待办", "还是空的。")
        await todo.finish(body + f"\n\n{summarize(tasks)}")
    tasks = store.active_tasks()
    if not tasks:
        await todo.finish("📭 待办是空的。要记就发：我要做 <任务>")
    body = list_text(tasks, f"📋 待办（{len(tasks)} 条）")
    await todo.finish(body + f"\n\n{summarize(store.all_tasks())}")


async def _cmd_add(event: MessageEvent, action: Action) -> None:
    text = action.extra.get("rest") or ""
    text = text or action.value
    if not text.strip():
        await todo.finish("用法：/待办 做 <任务>，例如 /待办 做 修个 bug")
    await _add_from_quick(event, text, f"/待办 做 {text}", todo)


def _resolve_fuzzy(event: MessageEvent, target: str) -> tuple[Optional[dict[str, Any]], str]:
    """先按原文解析；原文找不到（或被拆成单字）时，再按「合并字间空格」的版本试一次。"""
    task, err = _resolve(event, target)
    if task is not None:
        return task, err
    merged = _deglue(target)
    if merged != target and parse_id(target) is None:
        return _resolve(event, merged)
    # 原文匹配到多条（多半是「修 复」被拆开导致的误匹配）也算找不到
    return task, err


async def _cmd_done(event: MessageEvent, action: Action) -> None:
    target = _deglue(action.target or "")
    if not target:
        await _cmd_list(event, Action("list", value="完成"))
    task, err = _resolve_fuzzy(event, target)
    if err:
        await todo.finish(err)
    if task["status"] == "done":
        await todo.finish(f"#{task['id']} {task['title']} 之前就完成过了。")
    await store.set_status(task["id"], "done", str(event.get_user_id()))
    left = len(store.active_tasks())
    tail = f"\n还剩 {left} 条待办。" if left else "\n🎉 清空了！"
    await todo.finish(f"✅ #{task['id']} {task['title']} 已完成{tail}")


async def _cmd_drop(event: MessageEvent, action: Action) -> None:
    task, err = _resolve_fuzzy(event, action.target or "")
    if err:
        await todo.finish(err)
    if task["status"] == "dropped":
        await todo.finish(f"#{task['id']} 之前就放弃过了。")
    await store.set_status(task["id"], "dropped", str(event.get_user_id()), detail=action.text[:100])
    await todo.finish(f"🚫 已放弃 #{task['id']} {task['title']}（记录还在，/待办 列表 全部 能看到）")


async def _cmd_del(event: MessageEvent, action: Action) -> None:
    target = _deglue(action.target or "")
    if target in ("结束的全部", "全部", "清空完成", "清空", "结束的"):
        finished = [t for t in store.all_tasks() if t["status"] in ("done", "dropped")]
        for t in finished:
            await store.set_status(t["id"], "dropped", str(event.get_user_id()), detail="批量清理")
        await todo.finish(f"🗑️ 清掉了 {len(finished)} 条已结束的待办。" if finished else "没有已结束的待办。")
    task, err = _resolve_fuzzy(event, target)
    if err:
        await todo.finish(err)
    if task["status"] in ("open", "doing"):
        await todo.finish(
            f"#{task['id']} 还没结束，不能直接删。\n"
            f"做完发 /待办 完成 {task['id']}；不做了发 /待办 放弃 {task['id']}。"
        )
    await store.set_status(task["id"], "dropped", str(event.get_user_id()), detail="删除")
    await todo.finish(f"🗑️ 已删除 #{task['id']} {task['title']}")


async def _cmd_start(event: MessageEvent, action: Action) -> None:
    task, err = _resolve_fuzzy(event, action.target or "")
    if err:
        await todo.finish(err)
    if task["status"] == "doing":
        await todo.finish(f"#{task['id']} 早就在进行中了。")
    await store.set_status(task["id"], "doing", str(event.get_user_id()))
    await todo.finish(f"🔄 #{task['id']} {task['title']} → 进行中")


async def _cmd_edit(event: MessageEvent, action: Action) -> None:
    task, err = _resolve(event, action.target or "")
    if err:
        await todo.finish(err)
    new_title = _fix_known_words(action.text or "")
    if not new_title:
        await todo.finish(f"用法：/待办 改 {task['id']} <新标题>")
    await store.update_task(
        task["id"], str(event.get_user_id()), title=new_title[: plugin_config.qqbot_todo_title_max]
    )
    await store.append_history(task["id"], str(event.get_user_id()), "edit", new_title[:100])
    await todo.finish(f"✏️ #{task['id']} 改名为：{new_title}")


async def _cmd_prio(event: MessageEvent, action: Action) -> None:
    task, err = _resolve(event, action.target or "")
    if err:
        await todo.finish(err)
    value = _deglue((action.text or "").strip()).lower().lstrip("!！~")
    mapping = {
        "紧急": 1, "最高": 1, "p0": 1, "1": 1,
        "高": 2, "p1": 2, "2": 2,
        "中": 3, "普通": 3, "p2": 3, "3": 3,
        "低": 4, "不急": 4, "p3": 4, "4": 4,
    }
    if value not in mapping:
        await todo.finish(f"优先级写「紧急 / 高 / 中 / 低」，或 1~4。\n例：/待办 优先级 {task['id']} 高")
    priority = mapping[value]
    await store.update_task(task["id"], str(event.get_user_id()), priority=priority)
    await store.append_history(task["id"], str(event.get_user_id()), "prio", value)
    await todo.finish(
        f"{line({**task, 'priority': priority})}  已设为「{PRIORITY_LABELS[priority]}」"
    )


async def _cmd_note(event: MessageEvent, action: Action) -> None:
    task, err = _resolve(event, action.target or "")
    if err:
        await todo.finish(err)
    note = _normalize_free_text(action.text or "")
    if not note:
        if task.get("note"):
            await todo.finish(f"#{task['id']} 的备注：\n{task['note']}")
        await todo.finish(f"用法：/待办 备注 {task['id']} <文字>")
    await store.update_task(task["id"], str(event.get_user_id()), note=note[:1000])
    await store.append_history(task["id"], str(event.get_user_id()), "note", note[:100])
    await todo.finish(f"📝 #{task['id']} 备注已更新。")


async def _cmd_tag(event: MessageEvent, action: Action) -> None:
    task, err = _resolve(event, action.target or "")
    if err:
        await todo.finish(err)
    # 标签按空白 / 逗号拆；「#群 管」这种单字拆分也合成一个标签
    raw = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff](?:\s|$))", "", action.text or "")
    tags = [t.strip().lstrip("#＃") for t in re.split(r"[\s，,]+", raw) if t.strip().lstrip("#＃")][:5]
    if not tags:
        current = "".join(f" #{t}" for t in task["tags"]) or "（无）"
        await todo.finish(f"#{task['id']} 当前标签：{current}\n用法：/待办 标签 {task['id']} #群管 #插件")
    await store.update_task(task["id"], str(event.get_user_id()), tags=tags)
    await store.append_history(task["id"], str(event.get_user_id()), "tag", " ".join(tags))
    await todo.finish(f"🏷️ #{task['id']} 标签：" + "".join(f" #{t}" for t in tags))


async def _cmd_remind(event: MessageEvent, action: Action) -> None:
    raw = f"{action.target or ''} {action.text or ''}".strip()
    if not raw:
        await todo.finish("用法：/待办 提醒 <编号或关键词> <时间>，如 /待办 提醒 3 明天 9 点")
    # 从右往左试：切出一段能解析成时间的后缀，前面是目标
    task, when_text, when_ts = None, "", None
    for cut in range(len(raw), 0, -1):
        prefix, suffix = raw[:cut].strip(), raw[cut:].strip()
        if not suffix:
            continue
        ts, err = parse_when(suffix)
        if ts is None:
            continue
        candidate, find_err = _resolve(event, prefix)
        if candidate is not None:
            task, when_text, when_ts = candidate, suffix, ts
            break
    if task is None:
        ts, when_err = parse_when(raw)
        if ts is None and "看不懂" in when_err:
            await todo.finish(when_err)
        await todo.finish("没认出「哪条待办」或「什么时间」。\n用法：/待办 提醒 <编号或关键词> <时间>，如 /待办 提醒 3 明天 9 点")
    assert when_ts is not None
    if when_ts <= int(time.time()) + 30:
        await todo.finish("这个时间已经过去了，换一个吧。")
    await store.set_reminder(task["id"], str(event.get_user_id()), when_ts)
    await todo.finish(
        f"⏰ #{task['id']} {task['title']}\n"
        f"将在 {confirm_time(when_ts)}（{fmt_relative(when_ts)}）私聊提醒你。"
    )


def confirm_time(ts: int) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


async def _cmd_record(event: MessageEvent, action: Action) -> None:
    raw = f"{action.target or ''} {action.text or ''}".strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await todo.finish("用法：/待办 记录 <编号> <DSH 任务 ID 或链接>")
    task, err = _resolve(event, parts[0])
    if err:
        await todo.finish(err)
    value = parts[1].strip()[:200]
    await store.update_task(task["id"], str(event.get_user_id()), dsh_task_id=value)
    await store.append_history(task["id"], str(event.get_user_id()), "record", value)
    await todo.finish(f"🔗 #{task['id']} 已关联 DSH：{value}")


# ---------------------------------------------------------------- 导出（交接给 DSH）
def _write_handoff(text: str) -> tuple[bool, str]:
    """按配置写交接文件；返回 ``(是否写入, 给用户看的说明)``。"""
    raw_dir = (plugin_config.qqbot_todo_dsh_dir or "").strip()
    if not raw_dir:
        return False, "（未配置 qqbot_todo_dsh_dir，只发消息不写文件）"
    target = Path(raw_dir) / f"todo_handoff.{'json' if text.lstrip().startswith('{') else 'md'}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        logger.info(f"[qqbot_todo] 交接文件已写入 {target}")
        return True, f"📄 已写入 {target}（{target.stat().st_size} 字节）"
    except Exception as e:  # noqa: BLE001 —— 写不了文件也要把内容发出去
        logger.warning(f"[qqbot_todo] 写交接文件 {target} 失败：{e}")
        return False, f"⚠️ 写文件失败：{e}（内容见下）"


async def _cmd_export(event: MessageEvent, action: Action) -> None:
    raw_mode = ((action.value or "") + " " + (action.extra.get("rest") or "")).strip().lower()
    mode = {
        "全部": "all", "所有": "all", "历史": "all", "all": "all",
        "完成": "done", "已完成": "done", "done": "done",
        "json": "json",
        "md": "md", "markdown": "md",
        "": "active", "待办": "active", "未完成": "active", "active": "active",
    }.get(raw_mode, raw_mode)
    if mode not in ("active", "done", "all", "json", "md"):
        await todo.finish("用法：/待办 导出 [json|完成|全部]，不写参数 = 未完成的待办")
    as_json = mode == "json"
    if mode in ("json", "md"):
        mode = "active"
    tasks = _tasks_for(mode)
    if not tasks:
        await todo.finish("📭 没有可导出的待办。")

    title = handoff_title_for(event)
    if as_json:
        body = json.dumps(export_json(tasks, title, mode), ensure_ascii=False, indent=2) + "\n"
    else:
        body = export_markdown(tasks, title)
    written, note = _write_handoff(body)
    store.save_export_snapshot(store.snapshot(tasks))
    head = (
        f"📦 交接「{title}」：{len(tasks)} 条\n{note}\n"
        "在 DSH 里说：读这个文件，按里面的事项开始做。"
    )
    await todo.send(head)
    await todo.finish(MessageSegment.text(body))


async def _cmd_title(event: MessageEvent, action: Action) -> None:
    key = (str(event.get_user_id()), event.get_session_id())
    title = _fix_known_words(action.value or "")
    if not title:
        await todo.finish(f"当前交接标题：{handoff_title_for(event)}\n用法：/待办 交接 <标题>")
    _handoff_titles[key] = title[:60]
    await todo.finish(f"✏️ 交接标题已设为「{_handoff_titles[key]}」。\n发 /待办 导出 生成交接文件。")


# ---------------------------------------------------------------- 帮助
async def _cmd_help(event: MessageEvent, action: Action) -> None:
    await todo.finish(HELP)


_HANDLERS = {
    "list": _cmd_list,
    "add": _cmd_add,
    "done": _cmd_done,
    "del": _cmd_del,
    "drop": _cmd_drop,
    "start": _cmd_start,
    "edit": _cmd_edit,
    "prio": _cmd_prio,
    "note": _cmd_note,
    "tag": _cmd_tag,
    "remind": _cmd_remind,
    "record": _cmd_record,
    "export": _cmd_export,
    "title": _cmd_title,
    "help": _cmd_help,
}


def _tasks_for(mode: str) -> list[dict[str, Any]]:
    tasks = store.all_tasks()
    if mode == "done":
        return [t for t in tasks if t["status"] == "done"]
    if mode == "all":
        return tasks
    return [t for t in tasks if t["status"] in ("open", "doing")]


# ---------------------------------------------------------------- 快捷短语：我做完了 3
_DONE_SHORT_RE = re.compile(
    r"^\s*(?P<word>做完了|做完了没|完成了|搞定|不做了|搁置|开始做了|开始了|进行中)\s*"
    r"(?:第\s*)?(?P<rest>[#＃]?\w.*)$",
    re.S,
)
_SHORT_ACTIONS = {"做完了": "done", "做完了没": "done", "完成了": "done", "搞定": "done"}


def _match_done_word(text: str) -> Optional[tuple[str, str]]:
    """「我做完了 3」「完成 3」-> ``("done", "3")``；没有编号的一律不认，避免误伤聊天。"""
    text = text.strip()
    if text.startswith("我"):
        text = text[1:].lstrip("，, ")
    m = _DONE_SHORT_RE.match(text)
    if not m:
        return None
    word, rest = m.group("word"), m.group("rest").strip()
    if not rest:
        return None
    if word in _SHORT_ACTIONS:
        return "done", rest
    if word in ("不做了", "搁置"):
        return "drop", rest
    return "start", rest


done_short = on_message(rule=_superuser_rule() & Rule(_done_short_rule), priority=4, block=True)


@done_short.handle()
async def handle_done_short(event: MessageEvent) -> None:
    matched = _match_done_word(_strip_command_start(_plain(event)))
    if matched is None:
        return
    name, rest = matched
    words = rest.split(maxsplit=1)
    action = Action(name, target=words[0], text=words[1] if len(words) > 1 else "")
    await _HANDLERS[name](event, action)


# ---------------------------------------------------------------- 到点提醒
async def _remind_tick() -> None:
    """每分钟检查一次：到点未提醒的待办，私聊创建人。"""
    if not plugin_config.qqbot_todo_remind_enabled:
        return
    pending = store.pending_reminders()
    if not pending:
        return
    marked: set[int] = set()
    for task in pending:
        uid = str(task.get("source", {}).get("user_id") or task["created_by"])
        text = (
            f"⏰ 提醒 #{task['id']} {task['title']}\n"
            f"/待办 完成 {task['id']}   /待办 放弃 {task['id']}   /待办 提醒 {task['id']} 3天后"
        )
        if await _send_private(uid, MessageSegment.text(text)):
            marked.add(task["id"])
    await store.mark_reminded(marked)


if scheduler.get_job("qqbot_todo_remind") is None:
    scheduler.add_job(
        _remind_tick,
        "interval",
        seconds=60,
        id="qqbot_todo_remind",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
    )


# ---------------------------------------------------------------- 每日推送
async def _daily_push() -> None:
    if not plugin_config.qqbot_todo_daily_enabled:
        return
    tasks = store.active_tasks()
    if not tasks:
        return
    body = list_text(tasks, f"☀️ 今日待办（{len(tasks)} 条）") + "\n\n做完就发：/待办 完成 <编号>"
    for uid in sorted(_superusers()):
        await _send_private(uid, MessageSegment.text(body))


_hour, _minute = split_hhmm(plugin_config.qqbot_todo_daily_time)
if _hour is not None and scheduler.get_job("qqbot_todo_daily") is None:
    scheduler.add_job(
        _daily_push,
        "cron",
        hour=_hour,
        minute=_minute,
        id="qqbot_todo_daily",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
    )


# ---------------------------------------------------------------- 给其他插件 / DSH 用的接口
def active_tasks() -> list[dict[str, Any]]:
    """其他插件可直接用：``require("qqbot_todo").active_tasks()``。"""
    return store.active_tasks()


def export_handoff(mode: str = "active", title: str = "待办交接") -> str:
    """生成交接 Markdown（不写文件），供其他插件 / 脚本调用。"""
    return export_markdown(_tasks_for(mode), title)


def fresh_tasks(now: Optional[float] = None) -> list[dict[str, Any]]:
    """最近 30 天内新建 / 变更过的待办（DSH 侧只关心这些）。"""
    return [t for t in _tasks_for("active") if is_fresh(t, now)]

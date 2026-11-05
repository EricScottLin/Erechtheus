"""命令表收集 + 打错时的纠错提示（thefuck 风格的 QQ 版）。

命令表来源（运行时动态收集，不用维护清单）：

1. 遍历 ``nonebot.get_loaded_plugins()``，看每个 matcher 的 rule 里有没有
   ``CommandRule``，取它的 ``cmds``（主名在前、别名在后）；
2. 再用插件 ``PluginMetadata.usage`` 里出现的 ``/xxx`` 补充「仅写在帮助里」的命令。

什么时候提示：消息以命令前缀开头（``/``、``!`` 等）、首段文字**不是任何已注册命令**、
并且找到足够相似的候选时，回一条「你是不是想用」。
"""

import re
from typing import Iterable, Optional

import nonebot
from nonebot.log import logger

from . import fuzzy

# 命令前缀集合（NoneBot 的 command_start，默认 {" / "}）
_DEFAULT_STARTS = {"/"}


def command_starts() -> set[str]:
    try:
        return set(nonebot.get_driver().config.command_start) or _DEFAULT_STARTS
    except Exception:  # noqa: BLE001
        return _DEFAULT_STARTS


def _iter_command_rules(matcher) -> Iterable[tuple[str, ...]]:
    """从 matcher 的 rule 里挖出 CommandRule 的命令元组。"""
    rule = getattr(matcher, "rule", None)
    if rule is None:
        return
    for checker in getattr(rule, "checkers", []) or []:
        call = getattr(checker, "call", None) or checker
        cmds = getattr(call, "cmds", None)
        if not cmds:
            continue
        for item in cmds:
            if isinstance(item, str):
                yield (item,)
            elif isinstance(item, (tuple, list)) and item:
                yield tuple(str(x) for x in item)


def _usage_line(usage: str, name: str) -> str:
    """从插件 usage 里找「/命令」那一行，截成一句话说明。

    usage 排版像这样::

        /禁言 @用户 [时长]   指定时长禁言，如 /禁言 @他 30m
        /烟 @用户          随机禁言

    要处理的情况：命令名后面的**参数占位符**（``@用户``、``[时长]``、``<xxx>``）
    要去掉；同一行里命令方后面通常是多个空格再把说明对齐，所以按「连续 2 个以上
    空白」切第一段当说明。
    """
    for line in (usage or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "（", "(", "|")):
            continue
        m = re.match(r"^[\/／]([A-Za-z0-9\u4e00-\u9fff_]+)", stripped)
        if not m or m.group(1) != name:
            continue
        rest = stripped[m.end():]
        # 去掉开头的参数占位符：@用户 / [时长] / <id> / {x} / （别名） / 文字参数
        while True:
            new = re.sub(
                r"^\s*(?:@\S+|\[[^\]]*\]|<[^>]*>|\{[^}]*\}|[（(][^）)]*[）)]|\S+(?:\s+(?=\S))?)",
                "",
                rest,
                count=1,
            )
            # 只在「明显是占位符」时才继续剥；普通中文说明要留下
            if new == rest:
                break
            head = rest.strip().split(" ", 1)[0]
            if not head or head.startswith(("@", "[", "<", "{", "（", "(")):
                rest = new
                continue
            break
        rest = re.split(r"\s{2,}", rest.strip().lstrip("-—:："), maxsplit=1)[0].strip()
        if not rest:
            return ""
        return rest if len(rest) <= 34 else rest[:34] + "…"
    return ""


def collect_commands() -> list[fuzzy.CommandEntry]:
    """收集当前已加载插件的全部命令（主名 + 别名 + 说明）。

    两个去重点：

    - 同一个词在多个插件里既是主名又是别人的别名（如「烟」是 qqbot_admin 的
      主命令，qqbot_group 里又出现一次），只保留一条，别名合并；
    - 说明优先取 usage 里「/命令 这一行」的文字，拿不到再用插件 description。
    """
    slots: dict[str, dict] = {}
    alias_owner: dict[str, str] = {}
    order: list[str] = []

    for plugin in nonebot.get_loaded_plugins():
        meta = getattr(plugin, "metadata", None)
        plugin_desc = (getattr(meta, "description", "") or "").strip()
        if len(plugin_desc) > 40:
            plugin_desc = plugin_desc[:40] + "…"
        usage = getattr(meta, "usage", "") or ""

        for matcher in getattr(plugin, "matcher", []) or []:
            for cmds in _iter_command_rules(matcher):
                if not cmds or not cmds[0] or cmds[0] in command_starts():
                    continue
                name, aliases = cmds[0], cmds[1:]
                custom = _usage_line(usage, name)
                if name not in slots:
                    slots[name] = {
                        "aliases": set(),
                        # 有 usage 专属说明就用它，否则退回插件描述；都没有就留空
                        "description": custom or ("" if name != cmds[0] else plugin_desc),
                    }
                    order.append(name)
                elif custom and not slots[name]["description"]:
                    slots[name]["description"] = custom
                for alias in aliases:
                    alias_owner.setdefault(alias, name)
                    slots[name]["aliases"].add(alias)

    # 如果某条「命令」其实只是另一个命令的别名，且自己没有专属说明，就并进别名
    for name in list(order):
        owner = alias_owner.get(name)
        if owner and owner != name and not slots[name]["description"]:
            slots[owner]["aliases"].update(slots[name]["aliases"])
            slots[owner]["aliases"].add(name)
            slots[owner]["aliases"].discard(owner)
            del slots[name]
            order.remove(name)

    return [
        fuzzy.CommandEntry(
            name=name,
            aliases=tuple(sorted(slots[name]["aliases"])),
            description=slots[name]["description"],
        )
        for name in order
    ]


def split_command(raw: str, starts: Optional[set[str]] = None) -> tuple[str, str]:
    """把 ``"/签名 abc"`` 拆成 ``("/", "签名 abc")``；没有前缀返回 ``("", raw)``。"""
    starts = starts or command_starts()
    for prefix in sorted(starts, key=len, reverse=True):
        if prefix and raw.startswith(prefix):
            return prefix, raw[len(prefix):]
    return "", raw


def _resolve_alias(entries: list[fuzzy.CommandEntry]) -> dict[str, str]:
    """别名 -> 主命令名，用于判断「用户发的其实是某个别名」。"""
    table: dict[str, str] = {}
    for entry in entries:
        table[entry.name] = entry.name
        for alias in entry.aliases:
            table.setdefault(alias, entry.name)
    return table


def build_hint(raw: str, limit: int = 4) -> Optional[str]:
    """给一条「命令打错」的消息生成纠错提示；不需要提示时返回 None。"""
    prefix, body = split_command(raw)
    if not prefix or not body.strip():
        return None

    # 首段是用户输入的命令词；只认「只有命令 + 少量参数」的短消息，避免正常聊天误触
    question = body.strip().split()[0]
    if not question or len(question) > 20:
        return None

    entries = collect_commands()
    if not entries:
        return None
    if question in _resolve_alias(entries):
        return None  # 命令是有效的（主名或别名），交给正常 matcher

    suggestions = fuzzy.suggest(question, entries, limit=limit)
    if not suggestions:
        return None
    logger.info(
        f"[qqbot_ai] 命令纠错：{question!r} -> "
        f"{[(s.entry.name, round(s.score)) for s in suggestions]}"
    )
    return fuzzy.format_suggestions(question, suggestions, command_start=prefix)


# 明显的「像命令」特征：全部是命令前缀开头的消息才处理；
# 单字符或纯符号不提示，避免刷屏
_TYPOSO_RE = re.compile(r"^[\w\u4e00-\u9fff]{1,20}$")


def looks_like_command_typo(raw: str) -> bool:
    """粗判：带命令前缀、首段像命令词、不是自然语言句子。"""
    prefix, body = split_command(raw)
    if not prefix:
        return False
    head = body.strip().split()[0] if body.strip() else ""
    return bool(head) and bool(_TYPOSO_RE.match(head))

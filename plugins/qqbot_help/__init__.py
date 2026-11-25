"""统一帮助：任何命令都能 <命令> help，单独写功能名也能看到详情。

三种入口：

1. ``/help``、``/帮助``、``/菜单`` —— 列出全部命令（按插件分组）
2. ``<命令名> help`` / ``help <命令名>`` —— 该命令的详情
3. 单独发一个**能匹配到功能/命令、但直接发执行不了**的名字 —— 返回它的详情

「菜单 / 帮助」只在**参数确实是一个命令或功能名**时才接管。理由见
:func:`_looks_like_help_query`：这两个词也是别的机器人的菜单命令，别家 bot
的输出（多行、带 ``|`` 的渲染文本）以「菜单」开头时会被当成参数截走，
2026-11-25 就因此刷出过「参数 … 匹配失败」和「没找到「| 导航…」」。

用法说明由各插件通过 ``describe(...)`` 自愿登记；没登记的插件也会被列出
（只显示命令名），不会漏。
"""

from __future__ import annotations

import re
import sys

import nonebot
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg, EventMessage
from nonebot.rule import Rule

from . import menu, render

# 这里不能 import qqbot_group（插件目录独立），用运行时查找，缺失就退化
# ---------------------------------------------------------------- 说明登记表
# key: 命令名（小写，不含前缀）  value: {"desc": 一句话, "usage": [...], "feature": 功能key}
_DESCRIPTIONS: dict[str, dict] = {}


def describe(
    *cmds: str,
    desc: str = "",
    usage: list[str] | None = None,
    feature: str = "",
    group: str = "",
) -> None:
    """登记命令说明。多个别名可以一次登记（会共享同一份说明）。"""
    item = {"desc": desc, "usage": list(usage or []), "feature": feature, "group": group}
    # 主名必登记；别名同样登记（共享同一份说明），这样 /help B50 也能查到，
    # 而且 _command_map 会把它们当成「已知命令」，不会被当成别家 bot 的卡片丢掉。
    for cmd in cmds:
        if cmd:
            _DESCRIPTIONS[str(cmd).strip().lstrip("/").lower()] = item


def _load_catalog() -> None:
    """载入集中说明表（catalog.py）。"""
    try:
        from .catalog import CATALOG
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_help] 载入说明表失败：{e}")
        return
    for cmds, desc, usage, feature, group in CATALOG:
        describe(*cmds, desc=desc, usage=usage, feature=feature, group=group)


_load_catalog()


# ---------------------------------------------------------------- 命令采集
def _iter_commands() -> list[tuple[str, set[str], str]]:
    """返回 [(插件名, {命令名...}, 展示名)]，去重且保持稳定顺序。"""
    out: list[tuple[str, set[str], str]] = []
    try:
        plugins = nonebot.get_loaded_plugins()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_help] 采集命令失败：{e}")
        return out

    for plugin in plugins:
        names: set[str] = set()
        for matcher in getattr(plugin, "matcher", []) or []:
            rule = getattr(matcher, "rule", None)
            for checker in getattr(rule, "checkers", []) or []:
                call = getattr(checker, "call", None) or checker
                for item in getattr(call, "cmds", None) or []:
                    if isinstance(item, str):
                        names.add(item)
                    elif isinstance(item, (tuple, list)):
                        names.update(str(x) for x in item if x)
        if names:
            out.append((str(plugin.name), names, str(plugin.name)))
    return out


def _command_map() -> dict[str, str]:
    """命令名（小写）-> 所属插件名。

    除了**已加载插件**注册的命令，还要并上 catalog 里登记的名字：
    插件没加载/被禁用的群里，``/菜单 今日猪猪`` 也应该能查到说明，
    不能因为「命令表里没有」就被当成别家的卡片直接丢掉。
    """
    mapping: dict[str, str] = {
        str(cmd).strip().lstrip("/").lower(): "catalog"
        for cmd in _DESCRIPTIONS
        if str(cmd).strip()
    }
    for plugin_name, names, _ in _iter_commands():
        for name in names:
            mapping.setdefault(name.lower(), plugin_name)
    return mapping


def _aliases() -> dict[str, str]:
    """别名（小写）-> 主命令名。``/B50 help``、``/help b50`` 这类也要能查。"""
    out: dict[str, str] = {}
    try:
        from .catalog import CATALOG
    except Exception:  # noqa: BLE001
        return out
    for cmds, _desc, _usage, _feature, _group in CATALOG:
        if not cmds:
            continue
        main = str(cmds[0]).lower()
        for alias in cmds[1:]:
            out.setdefault(str(alias).lower(), main)
    return out


# ---------------------------------------------------------------- 功能开关对接
def _feature_registry():
    plugin = nonebot.get_plugin("qqbot_group")
    if plugin is None:
        return None
    return getattr(plugin.module, "features", None)


def _ensure_feature_registrations() -> None:
    """让各业务插件的 ``features.ensure_registered()`` 跑一遍。

    qqbot_lyric / qqbot_todo / qqbot_roast 这些插件是惰性注册开关的：只有自己的
    命令被调用时才注册。帮助要按开关过滤，得先把它们都注册齐，否则「本群关掉的
    歌词接龙」会照样出现在总览里。
    """
    for plugin in nonebot.get_loaded_plugins():
        module = getattr(plugin, "module", None)
        name = getattr(module, "__name__", "") if module is not None else ""
        if not name:
            continue
        ensure = getattr(sys.modules.get(f"{name}.features"), "ensure_registered", None)
        if callable(ensure):
            try:
                ensure()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[qqbot_help] {name} 注册开关失败：{exc}")


def _all_features() -> list:
    registry = _feature_registry()
    if registry is None:
        return []
    try:
        _ensure_feature_registrations()
        registry.sync_external_features()
        return [registry._REGISTRY[k] for k in registry._ORDER]  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return []


def _find_feature(word: str):
    """按 key / 中文名 / 别名找功能。"""
    word = (word or "").strip().lower()
    if not word:
        return None
    for feature in _all_features():
        names = {feature.key.lower(), feature.name.lower()}
        names |= {str(a).lower() for a in feature.aliases}
        if word in names:
            return feature
    return None


def _feature_enabled(event: MessageEvent, key: str) -> bool | None:
    registry = _feature_registry()
    group_id = getattr(event, "group_id", None)
    if registry is None or group_id is None:
        return None
    try:
        _ensure_feature_registrations()
        return bool(registry.is_enabled(int(group_id), key))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 图片菜单
# 总览/详情都是现场画的 PNG：本群开关不同、群不同，图就不一样，所以按
# (群号, 可见功能) 缓存少量结果，避免同一条 /help 反复渲染。
_OVERVIEW_CACHE: dict[tuple, bytes] = {}
_DETAIL_CACHE: dict[tuple, bytes] = {}


def _maimai_dx_open(group_id: int) -> bool:
    """科技是否对本群开放（插件没加载就当作未开放）。"""
    plugin = nonebot.get_plugin("qqbot_maimai_dx")
    check = getattr(plugin.module, "help_visible", None) if plugin is not None else None
    if check is None:
        return False
    try:
        return bool(check(int(group_id)))
    except Exception:  # noqa: BLE001
        return False


def _feature_visible(event: MessageEvent, feature: menu.Feature) -> bool:
    """这个功能在当前群/私聊里该不该出现在帮助里。"""
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        # 私聊没有群上下文：只展示不依赖开通范围的功能
        return not feature.gate
    if feature.gate == "maimai_dx" and not _maimai_dx_open(group_id):
        return False
    if feature.switch and _feature_enabled(event, feature.switch) is False:
        return False
    return True


def _switch_label(feature: menu.Feature) -> str | None:
    """详情页顶部的开关标签；没有开关返回 None。"""
    if not feature.switch:
        return None
    registered = next((f for f in _all_features() if f.key == feature.switch), None)
    return registered.name if registered is not None else feature.name


def _hidden_reason(event: MessageEvent, feature: menu.Feature) -> str:
    if feature.gate == "maimai_dx":
        if getattr(event, "group_id", None) is None:
            return f"「{feature.name}」现在用不了。"
        return f"本群没有「{feature.name}」这个功能。"
    label = _switch_label(feature) or feature.name
    return f"本群没开「{feature.name}」，找群管发 /开启 {label}。"


def _visible_features(event: MessageEvent) -> list[menu.Feature]:
    return [f for f in menu.FEATURES if _feature_visible(event, f)]


def _overview_image(event: MessageEvent) -> bytes | None:
    """总览图；字体缺失或渲染失败时返回 None，调用方退回文字。"""
    if not render.available():
        return None
    visible = _visible_features(event)
    group_id = getattr(event, "group_id", None)
    key = (group_id, tuple(f.name for f in visible))
    cached = _OVERVIEW_CACHE.get(key)
    if cached is not None:
        return cached
    blocks: list[tuple[str, list[tuple[str, tuple[str, ...]]]]] = []
    for block_name, _color in menu.BLOCKS:
        rows = [(f.name, f.overview) for f in visible if f.block == block_name]
        if rows:
            blocks.append((block_name, rows))
    if not blocks:
        return None
    if group_id is None:
        footer = f"共 {len(visible)} 个功能 · 发 /help 功能名 看详情"
    else:
        footer = f"本群已开启 {len(visible)} 个功能 · 发 /本群功能 查看全部开关"
    try:
        png = render.render_overview(blocks, footer)
    except Exception as exc:  # noqa: BLE001 —— 出图失败不能拖垮帮助
        logger.warning(f"[qqbot_help] 总览出图失败：{type(exc).__name__}: {exc}")
        return None
    if len(_OVERVIEW_CACHE) > 24:
        _OVERVIEW_CACHE.clear()
    _OVERVIEW_CACHE[key] = png
    return png


def _detail_tag(feature: menu.Feature) -> str | None:
    """详情页顶部的状态标签；科技这类不暴露控制方式，返回 None。"""
    if feature.gate:
        return None
    label = _switch_label(feature)
    if label is None:
        return "本群未受开关控制 · 常开"
    return f"受 /开启 {label} 控制"


def _detail_image(feature: menu.Feature) -> bytes | None:
    tag = _detail_tag(feature)
    key = (feature.name, tag)
    cached = _DETAIL_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        png = render.render_detail(feature, tag=tag)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[qqbot_help] 详情出图失败：{type(exc).__name__}: {exc}")
        return None
    if len(_DETAIL_CACHE) > 64:
        _DETAIL_CACHE.clear()
    _DETAIL_CACHE[key] = png
    return png


async def _finish_page(matcher, event: MessageEvent, word: str) -> bool:
    """word 命中功能页就发图；返回 False 表示没命中、交给后面的文字逻辑。"""
    feature = menu.find_feature(word) or menu.find_by_command(word)
    if feature is None:
        return False
    if not _feature_visible(event, feature):
        await matcher.finish(_hidden_reason(event, feature))
    png = _detail_image(feature)
    if png is None:
        return False
    await matcher.finish(MessageSegment.image(png))
    return True


# ---------------------------------------------------------------- 渲染
_PREFIX = ("/",)


def _prefix() -> str:
    starts = nonebot.get_driver().config.command_start or {"/"}
    for p in starts:
        if p:
            return p
    return ""


def _cmd_help_text(cmd: str) -> str:
    """组装单个命令的说明。"""
    key = cmd.strip().lstrip("/").lower()
    meta = _DESCRIPTIONS.get(key) or {}
    plugin_name = _command_map().get(key, "")

    lines: list[str] = []
    title = f"{_prefix()}{cmd.strip().lstrip('/')}"
    if meta.get("desc"):
        lines.append(f"📖 {title} —— {meta['desc']}")
    else:
        lines.append(f"📖 {title}")
        if plugin_name:
            lines.append(f"（{plugin_name} 的命令，暂未写说明）")

    usage = meta.get("usage") or []
    if usage:
        lines.append("")
        lines.append("用法：")
        lines.extend(f"  {u}" for u in usage)

    feature = meta.get("feature") or ""
    feats = _all_features()
    if feature:
        feat = next((f for f in feats if f.key == feature), None)
        if feat is not None:
            lines.append("")
            lines.append(f"🔧 受本群开关「{feat.name}」控制（/开启 {feat.name}）")
    return "\n".join(lines)


def _feature_help_text(feature) -> str:
    """一个只受开关控制、没有独立命令的功能。"""
    lines = [f"🔧 {feature.name}"]
    # 该功能对应的命令说明（catalog 里用 feature key 关联）
    for cmd, meta in _DESCRIPTIONS.items():
        if meta.get("feature") == feature.key and meta.get("desc"):
            lines.append(f"{meta['desc']}")
            usage = meta.get("usage") or []
            if usage:
                lines.append("")
                lines.append("用法：")
                lines.extend(f"  {u}" for u in usage)
            break
    aliases = [a for a in feature.aliases if a]
    if aliases:
        lines.append(f"说法：{'、'.join(aliases[:12])}")
    lines.append("")
    lines.append(f"本群开关：/开启 {feature.name}、/关闭 {feature.name}")
    return "\n".join(lines)


_PLUGIN_LABELS = {
    "qqbot_group": "群管理",
    "qqbot_roast": "娱乐",
    "qqbot_meme": "表情/金句",
    "qqbot_repeat": "复读",
    "qqbot_pigverse": "猪猪",
    "qqbot_economy": "经济",
    "qqbot_core": "核心",
    "qqbot_todo": "工具",
    "qqbot_lyric": "歌词",
    "qqbot_ai": "AI",
    "qqbot_toolbox": "工具",
    "nonebot_plugin_rollpig_plus": "抽猪",
    "nonebot_plugin_bilibili": "视频",
    "nonebot_plugin_maimaidx": "舞萌",
    "qqbot_maimai_dx": "科技",
    "qqbot_djmax": "DJMAX",
    "nonebot_plugin_nbnhhsh": "缩写",
}


def _overview() -> str:
    """全部命令总览，按插件分组。"""
    groups: dict[str, list[str]] = {}
    for plugin_name, names, _ in _iter_commands():
        for name in names:
            meta = _DESCRIPTIONS.get(name.lower()) or {}
            group = meta.get("group") or _PLUGIN_LABELS.get(plugin_name, plugin_name)
            groups.setdefault(group, [])
            if name not in groups[group]:
                groups[group].append(name)

    # 有说明的分组优先（更可能是用户日常用的），再按数量排；
    # 帮助本身永远排最后 —— 排在中间点进去也还是自己。
    ordered = sorted(
        groups.items(),
        key=lambda kv: (
            1 if kv[0] == "帮助" else 0,
            0 if any(_DESCRIPTIONS.get(n.lower()) for n in kv[1]) else 1,
            -len(kv[1]),
            kv[0],
        ),
    )
    lines = ["📖 命令总览", "────────────"]
    total = 0
    for group, names in ordered:
        names = sorted(names)
        total += len(names)
        shown = names[:8]
        more = f" 等 {len(names)} 个" if len(names) > len(shown) else ""
        lines.append(f"{group}：{'、'.join(_prefix() + n for n in shown)}{more}")
    lines.append("────────────")
    lines.append(f"共 {total} 个命令（有的插件很短，只显示前几个；/help <命令> 看详情）")
    lines.append("看详情：<命令> help 或 help <命令>")
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口 1/2：help 命令
# 参数不像命令/功能名时不接管（交给 picmenu 自己的 help），见 _looks_like_help_query
help_cmd = on_command(
    "help",
    aliases={"帮助", "菜单", "命令"},
    priority=4,
    block=True,
)


def _clip(text: str, limit: int = 40) -> str:
    """给用户看的超长参数截断（避免把一大段渲染文本原样回显）。"""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


@help_cmd.handle()
async def handle_help(
    event: MessageEvent,
    msg: Message = EventMessage(),
    arg: Message = CommandArg(),
) -> None:
    # 「菜单 xxx」：xxx 不像命令/功能名就直接退出，一个字都不回。
    # 这些是别家 bot 的菜单卡片（多行、带 |），回「没找到…」等于把卡片原样回显。
    if not _help_query_ok(msg):
        logger.debug(f"[qqbot_help] 参数不像命令名，不接管：{msg.extract_plain_text()[:60]!r}")
        return
    word = arg.extract_plain_text().strip()
    if not word:
        png = _overview_image(event)
        if png is not None:
            await help_cmd.finish(MessageSegment.image(png))
        await help_cmd.finish(_overview())

    # help <功能> 出详情图；help <命令> 若属于某个功能，也出该功能的详情图
    await _finish_page(help_cmd, event, word)

    # help <命令>
    key = word.lstrip("/").lower()
    if key in _command_map() or key in _aliases():
        await help_cmd.finish(_cmd_help_text(word))
    feature = _find_feature(word)
    if feature is not None:
        await help_cmd.finish(_feature_help_text(feature))
    await help_cmd.finish(
        f"没找到「{_clip(word)}」。\n发 /help 看全部命令，或 <命令> help 看单个命令。"
    )


# ---------------------------------------------------------------- 入口 3：<命令> help
# 裸消息，priority 高于业务 matcher（业务多为 5-6），block=True 避免被当成参数
_HELP_TAIL_RE = re.compile(r"^(?P<word>.+?)\s+(?:help|帮助|怎么用|用法|使用说明)$", re.I)
_HELP_HEAD_RE = re.compile(r"^(?:help|帮助)\s+(?P<word>.+)$", re.I)


def _strip_prefix(text: str) -> str:
    starts = sorted(
        (s for s in (nonebot.get_driver().config.command_start or set()) if s),
        key=len,
        reverse=True,
    )
    out = text
    for s in starts:
        if out.startswith(s):
            return out[len(s) :].strip()
    return out.strip()


async def _help_rule(event: MessageEvent, msg: Message = EventMessage()) -> bool:
    raw = msg.extract_plain_text()
    text = _strip_prefix(raw.strip())
    if not text:
        return False
    # 多行 / 带 | 的是别家 bot 的菜单卡片，一个字都不接
    if _is_card_blob(raw):
        return False
    # 「help xxx」（带不带前缀都一样）交给 help_cmd，这里不重复处理
    if re.match(r"^(?:help|帮助|菜单)\s+\S", text, re.I):
        return False
    if _HELP_TAIL_RE.match(text):
        return True
    # 单独一个词：只有当它确实对应某个功能/命令、且单独执行不了时才接管
    if " " in text:
        return False
    return _should_help_single(text)


_help_msg = on_message(rule=_help_rule, priority=4, block=True)


@_help_msg.handle()
async def handle_single(event: MessageEvent, msg: Message = EventMessage()) -> None:
    text = _strip_prefix(msg.extract_plain_text().strip())

    m = _HELP_TAIL_RE.match(text)
    if m:
        word = m.group("word").strip()
        await _finish_page(_help_msg, event, word)
        if word.lstrip("/").lower() in _command_map() or word.lstrip("/").lower() in _aliases():
            await _help_msg.finish(_cmd_help_text(word))
        feature = _find_feature(word)
        if feature is not None:
            await _help_msg.finish(_feature_help_text(feature))
        await _help_msg.finish(f"没找到「{_clip(word)}」。发 /help 看全部命令。")

    m = _HELP_HEAD_RE.match(text)
    if m:
        word = m.group("word").strip()
        await _finish_page(_help_msg, event, word)
        if word.lstrip("/").lower() in _command_map() or word.lstrip("/").lower() in _aliases():
            await _help_msg.finish(_cmd_help_text(word))
        feature = _find_feature(word)
        if feature is not None:
            await _help_msg.finish(_feature_help_text(feature))
        await _help_msg.finish(f"没找到「{_clip(word)}」。发 /help 看全部命令。")

    # 单独词：功能详情
    await _finish_page(_help_msg, event, text)
    feature = _find_feature(text)
    if feature is not None:
        await _help_msg.finish(_feature_help_text(feature))
    await _help_msg.finish(_cmd_help_text(text))


def _should_help_single(word: str) -> bool:
    """单独发一个词时，是否由帮助接管。

    条件是「能匹配到功能或命令」，并且**单独发它执行不了**：
    - 命令名本身能执行（如 /签到），不接管，正常执行
    - 功能名（如「电猪」是开关别名、不是命令）单独发没反应，接管并给详情
    """
    key = word.lstrip("/").lower()
    if not key:
        return False
    is_command = key in _command_map()
    feature = _find_feature(word) or menu.find_feature(word)
    if feature is None:
        return False
    # 命令同时也是功能名时（如「表情」），让它执行，不接管
    return not is_command


# ---------------------------------------------------------------- 「菜单/帮助」的接管条件
# help / 帮助 / 菜单 同时是 other bot（picmenu 的 alc 命令、别家的 /菜单）的入口，
# 参数不是我们的命令名时接管，等于把别人输出的渲染文本（多行、带 | 和 mqqapi 链接）
# 当参数吃掉，结果就是「没找到「| 导航…」」这种刷屏。
_HELP_WORDS = ("help", "帮助", "菜单", "命令")
# 参数首行最多这么长才可能是裸命令名（「修 复 / 烟 的 冷 却」这类空格插入的也算）
_MAX_QUERY_CHARS = 24


def _is_card_blob(raw: str) -> bool:
    """这条消息是不是别家 bot 的菜单卡片（多行 / 带 ``|`` 的渲染文本）。

    卡片里的「菜单 | 导航 …」会被当成命令参数截走，回一句
    「没找到「| 导航…」」。判据只有两条：**行数 > 1**、或**含 ``|``**；
    用户自己真的会发的 ``菜单 | 导航`` 这种，也是别家在渲染，不接。
    """
    text = raw or ""
    if "|" in text or "｜" in text:
        return True
    return len([ln for ln in text.splitlines() if ln.strip()]) > 1


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _looks_like_help_query(raw: str) -> bool:
    """「菜单 X」「帮助 X」里的 X 像不像一个命令/功能名。

    只认**一行、不含 ``|``、很短**的首段文字 —— 那是用户真的在查东西
    （``/菜单 电猪``、``/帮助 今日猪猪``）。

    多行 / 含 ``|`` / 首行是一整句话的，都是别家 bot 的菜单卡片输出
    （2026-11-25：加群机器人回「菜单 | 导航 … 你的今日老婆是…」被截走），
    这时不接管：交给 picmenu 自己的 ``help`` 去回，我们一个字都不回。
    """
    text = (raw or "").strip()
    if not text or _is_card_blob(text):
        return False
    head = _first_line(text)
    if not head or len(head) > _MAX_QUERY_CHARS:
        return False
    if _strip_prefix(text) != text:
        return True  # 「/菜单 电猪」是明确的查询意图
    words = head.split()
    # 首词不是命令词（「今日老婆」「电猪」…）就交给别的插件
    if words and words[0].lstrip("/").lower() not in _HELP_WORDS:
        return False
    if len(words) <= 1:
        return True  # 裸命令词，出总览
    query = " ".join(words[1:]).strip()
    key = query.lower()
    if key in _command_map() or _find_feature(query) is not None or _menu_hit(query):
        return True
    # 空格插入的写法（「今 日 猪 猪」）：去空后再比一次
    squeezed = re.sub(r"\s+", "", key)
    if squeezed and squeezed != key:
        return squeezed in _command_map() or _find_feature(squeezed) is not None or _menu_hit(squeezed)
    return False


def _menu_hit(word: str) -> bool:
    """是不是图片菜单里的功能名或命令。"""
    return menu.find_feature(word) is not None or menu.find_by_command(word) is not None


def _help_query_ok(msg: Message) -> bool:
    """``help_cmd`` 的守卫：这条「菜单/帮助 …」到底该不该由我们回。"""
    raw = msg.extract_plain_text()
    text = _strip_prefix(raw.strip())
    if not text:
        return True  # 裸「/help」：出总览
    if _is_card_blob(raw):
        return False  # 别家 bot 的菜单卡片，一个字都不回
    return _looks_like_help_query(raw)


# ---------------------------------------------------------------- 吞掉解析失败提示
# picmenu-next 的「菜单/帮助」是 Alconna 命令，解析失败时 AlconnaRule 会把
# ``str(arpparams_unmatched)`` 当正文发出去，就是 2026-11-25 群里那句
# 「参数 你的今日老婆是「东方Project」琪露诺 匹配失败」—— 它每次都从
# `|` 和换行处断开，把别家的菜单卡片拆成「参数 A 匹配失败」+「参数 B 匹配失败」，
# 一个月刷了一百多条，用户反复排查都以为是我们的 bug。
#
# 这类提示对群友零价值（我们自己也不解析这些命令），全局注册一个扩展，
# 把 ``output_type == "error"`` 的输出吃掉；picmenu 的**正常菜单**走的是
# "help"/"shortcut" 类型，完全不受影响。
_QUIET_ALCONNA_EXT = None  # 模块级强引用：防止扩展被 GC 掉
_QUIET_APPLIED = False


def _quiet_alconna_errors() -> None:
    """静音 Alconna 命令的「参数 xxx 匹配失败」。

    2026-11-25 群里那句就是它：picmenu-next 的「菜单/帮助」是 Alconna 命令，
    别家 bot 回一张多行卡片时，Alconna 会从 ``|`` 和换行处断开，逐段发
    「参数 xxx 匹配失败」。一个月一百多条，反复排查都查错了地方（不在我们的代码里）。

    这里动两个地方，缺一不可：

    1. **关掉匹配器规则上的 ``auto_send``**：``AlconnaRule.handle`` 里一旦
       ``auto_send and may_help_text`` 成立就直接 ``send()`` 并 return False，
       扩展层（下面第 2 步）根本来不及拦。
    2. 注册全局扩展，把 ``output_type == "error"`` 的输出吞掉。

    只影响「解析失败」这一类。picmenu 的正常菜单、/help 等全部照旧。
    """
    global _QUIET_ALCONNA_EXT, _QUIET_APPLIED
    if _QUIET_APPLIED:
        return
    try:
        from nonebot_plugin_alconna import add_global_extension
        from nonebot_plugin_alconna.extension import Extension, OutputType
        from nonebot_plugin_alconna.uniseg import UniMessage
    except Exception as e:  # noqa: BLE001 —— 没装 alconna 就不需要
        logger.debug(f"[qqbot_help] 未安装 nonebot-plugin-alconna，跳过解析失败静音：{e}")
        return

    # 1) 让 Alconna 规则不再自动发送（否则 handle 里直接就把错误文本发出去了）
    try:
        plugin = nonebot.get_plugin("nonebot_plugin_picmenu_next")
        for matcher in getattr(plugin, "matcher", []) if plugin else []:
            for checker in getattr(getattr(matcher, "rule", None), "checkers", []) or []:
                rule = getattr(checker, "call", checker)
                if getattr(rule, "auto_send", False):
                    rule.auto_send = False
                    logger.info("[qqbot_help] 已关闭 picmenu 的自动发送（解析失败不再回显）")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qqbot_help] 关闭 picmenu 自动发送失败：{e}")

    # 2) 兜底：连规则层也没发出去的 error 输出，在输出转换阶段吃掉。
    #    id / priority 必须是类属性 —— 用 @property 时 add_global_extension 拿到的是
    #    property 对象而不是值，扩展会被静默丢弃（2026-11-25 踩过）。
    class _QuietAlconnaErrors(Extension):
        id = "qqbot_help:quiet_param_errors"
        priority = 0  # 高于 DefaultExtension(16)，先于它处理

        async def output_converter(self, output_type: OutputType, content: str) -> UniMessage:
            if output_type == "error":
                logger.debug(f"[qqbot_help] 已吞掉命令解析失败提示：{content[:60]!r}")
                return UniMessage()
            return UniMessage(content)

    try:
        _QUIET_ALCONNA_EXT = _QuietAlconnaErrors()
        add_global_extension(_QuietAlconnaErrors)
        _QUIET_APPLIED = True
        logger.info("[qqbot_help] 已静音 Alconna 的命令解析失败提示")
    except Exception as e:  # noqa: BLE001 —— 注册失败不能影响帮助本身
        logger.warning(f"[qqbot_help] 静音解析失败提示失败：{e}")


def _apply_quiet_on_startup() -> None:
    """picmenu 可能晚于本插件加载，启动时再补一次。"""
    _quiet_alconna_errors()


_quiet_alconna_errors()
try:
    nonebot.get_driver().on_startup(_apply_quiet_on_startup)
except Exception as e:  # noqa: BLE001
    logger.debug(f"[qqbot_help] 注册启动钩子失败：{e}")

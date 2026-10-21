"""QQBot 工具箱插件。

提供四类基础命令，用于验证消息链路是否打通、以及日常排查：

- ``/ping``    探活，测量消息链路与接口往返延迟（拥堵情况）
- ``/echo``    原样回显，验证「收到 -> 处理 -> 发出」全链路
- ``/指令``    文本帮助菜单：**按指令列出所有用法**，``/指令 <指令名>`` 看单条详情
                （``/help``、``帮助``、``菜单`` 由 nonebot-plugin-picmenu-next 提供图片菜单）
- ``/status``  查询运行状态（收发消息数 / 已运行时长）

遵循 NoneBot 官方插件编写规范：
https://nonebot.dev/docs/tutorial/create-plugin
"""

import re
import time
import unicodedata

from nonebot import on_command, get_loaded_plugins, get_plugin_config
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="QQBot 工具箱",
    description="指令菜单、探活与排查：/指令 看我所有能做的事",
    usage="""\
/指令            我能做什么（按指令列出全部用法）
/指令 <指令名>    看某条指令的详细用法，如 /指令 签到
/help            图片版完整菜单
/ping            看看我还在不在、回应快不快
/status          运行状态：收发消息数、已连续运行多久
/echo <内容>      原样回显，验证消息链路""",
    type="application",
    homepage="https://github.com/EricScottLin/Erechtheus",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)


# ---------------------------------------------------------------- 工具函数
def _reply_prefix(event: MessageEvent) -> Message:
    """按配置决定是否在回复前面 @ 发起者。"""
    if plugin_config.qqbot_toolbox_at_sender and event.is_tome() is False and event.get_user_id():
        return MessageSegment.at(event.get_user_id()) + MessageSegment.text(" ")
    return Message()


# ---------------------------------------------------------------- /ping
ping = on_command("ping", priority=5, block=True)


def _congestion(ms: float) -> str:
    """按延迟给一句拥堵评价。"""
    if ms < 50:
        return "🚀 畅通无阻，爱琴海无风"
    if ms < 200:
        return "✅ 正常，微澜"
    if ms < 500:
        return "🐌 有点堵，船在排队"
    if ms < 1000:
        return "😰 堵得慌，港口拥挤"
    return "🆘 雅典港大堵船，建议歇会儿"


@ping.handle()
async def handle_ping(bot: Bot, event: MessageEvent) -> None:
    """探活：测量接口往返与消息链路延迟。"""
    prefix = _reply_prefix(event)

    # 接口往返：NoneBot -> LLBot -> NoneBot
    t0 = time.perf_counter()
    try:
        await bot.call_api(
            "get_status", _timeout=plugin_config.qqbot_toolbox_status_timeout
        )
        api_ms = (time.perf_counter() - t0) * 1000
    except Exception:  # noqa: BLE001 —— 探活失败也要给个回应
        api_ms = None

    # 链路延迟：QQ 发出 -> LLBot -> OneBot WS -> NoneBot 收到
    link_ms = (
        (time.time() - event.time) * 1000 if getattr(event, "time", None) else None
    )

    if api_ms is None and link_ms is None:
        await ping.finish(prefix + "🏓 pong——活着，但延迟探不出来，链路可能有问题")

    # 对外只说人话：不说 LLBot / OneBot / WS 这些内部名词
    lines = ["🏓 pong，还活着"]
    if link_ms is not None:
        lines.append(f"  你发我收到：{link_ms:.0f}ms")
    if api_ms is not None:
        lines.append(f"  我回你一句：{api_ms:.0f}ms")
    worst = max(x for x in (link_ms, api_ms) if x is not None)
    lines.append(f"  {_congestion(worst)}")
    await ping.finish(prefix + "\n".join(lines))


# ---------------------------------------------------------------- /echo
echo = on_command("echo", priority=5, block=True)


@echo.handle()
async def handle_echo(event: MessageEvent, arg: Message = CommandArg()) -> None:
    """原样回显用户输入的内容。"""
    text = arg.extract_plain_text().strip()
    if not text:
        await echo.finish(_reply_prefix(event) + "用法：/echo <内容>，例如 /echo 你好")
    prefix = _reply_prefix(event)
    await echo.finish(prefix + f"收到：{text}")


# ---------------------------------------------------------------- /指令（文本帮助菜单）
# 图片菜单由 nonebot-plugin-picmenu-next 接管 /help、/帮助、/菜单；
# 这里提供一份纯文本、零渲染依赖的「保底」菜单。
help_cmd = on_command("指令", aliases={"指令列表", "cmds"}, priority=5, block=True)


def _plugin_title(p) -> str:
    """插件展示名：优先 PluginMetadata.name，否则包名。"""
    meta = getattr(p, "metadata", None)
    return meta.name if meta and meta.name else p.name


def _visible_plugins() -> list:
    """参与菜单展示的插件。

    过滤条件：
      - type == "library"：localstore / apscheduler / alconna 等基础库，
        它们没有面向用户的指令，且 usage 里是给开发者的代码示例。
      - 没有任何 usage 的插件：展示出来也无内容可看。
    """
    plugins = []
    for p in get_loaded_plugins():
        meta = getattr(p, "metadata", None)
        if meta is not None and meta.type == "library":
            continue
        if meta is None or not (meta.usage or "").strip():
            continue
        plugins.append(p)
    return sorted(plugins, key=_plugin_title)


# 指令行识别。
# 只认「行首是命令形态」的行，宁可漏也不要把散文切错：
#   /xxx ...            斜杠命令（最可靠）
#   xxx <说明>          裸中文指令，后面必须跟空格+说明
# 刻意不解析斜杠命令后面的参数：usage 里 "/购买 <编号>      购买鱼饵"
# 的参数列与说明列之间只是空格，无法可靠区分，故整行原文保留。
_SLASH_CMD = re.compile(r"^(/[^\s]+)\s*(.*)$")
# 允许 "/" 出现在指令名中，以识别「单抽/十连/百连」这种多指令共用一行的写法
_BARE_CMD = re.compile(r"^([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z/]{1,11})\s+(.+)$")
# 触发词说明行（如「说「精致睡眠」  随机禁言」）不是指令，直接跳过。
# 注意：只检查行首，不能检查整行 —— 否则说明里带括号/逗号的指令
# （如「/撤 撤回我上一条消息（仅管理员）」）会被误杀。
_TRIGGER_HINT = ("说「", "说\"", "发送「", "「")


def _usage_lines(p) -> list[str]:
    """插件的用法行：优先 PluginMetadata.usage，其次 description。"""
    meta = getattr(p, "metadata", None)
    if meta is None:
        return []
    if meta.usage:
        return [ln.rstrip() for ln in meta.usage.strip().splitlines() if ln.strip()]
    return [meta.description] if meta.description else []


def _plugin_cmds(p) -> list[tuple[str, str]]:
    """从插件 usage 抽取 [(指令, 完整原文)]，原文保留以便原样展示。

    判定规则（保守，宁可少列也不错列）：
      - 以 "/" 开头 → 一定是指令
      - 裸中文指令：2~8 个汉字 + 空格 + 说明
      - 行首是「说「xxx」」/括号提示/散文的，跳过
    兜底：若整段 usage 是一行「散文式」说明（用；或 / 分隔的多种用法），
    则按分隔符拆成多行展示，避免整个插件在菜单里消失。
    """
    out: list[tuple[str, str]] = []
    for line in _usage_lines(p):
        s = line.strip()
        if not s:
            continue
        # 括号开头是补充说明，不是指令
        if s.startswith(("（", "(", "-", "·", "#")):
            continue
        # 触发词说明行：只看行首，避免误杀说明里含括号的指令
        if s.startswith(_TRIGGER_HINT):
            continue

        m = _SLASH_CMD.match(s)
        if m:
            out.append((m.group(1), s))
            continue

        m = _BARE_CMD.match(s)
        if m:
            head = m.group(1)
            # "单抽/十连/百连   消耗月币抽奖"：多个指令共用一行，
            # 拆成多条，说明挂在最后一条上（原文按显示宽度对齐会错位，故重建）
            if "/" in head:
                names = [x for x in head.split("/") if x]
                rest = s[len(head):].strip()
                for name in names[:-1]:
                    out.append((name, name))
                last = names[-1] if names else head
                out.append((last, f"{last}  {rest}" if rest else last))
                continue
            out.append((m.group(1), s))
            continue

        # 兜底：整段 usage 是散文式说明（无行首指令形态）时，
        # 按 ； 拆成多段展示，尽量不漏掉插件。
        # 注意：不含 "/" 的分段说明里 "xxx / yyy" 的写法，不做二次切分，
        # 否则 "/bilibili rank"、"单抽/十连/百连" 这类会被切坏。
        for seg in re.split(r"[；;]", s):
            seg = seg.strip()
            if not seg:
                continue
            m = re.search(r"(/[a-zA-Z][^\s]*)", seg)
            key = m.group(1) if m else seg[:12]
            out.append((key, seg))

    return out


def _cmd_index(plugins: list) -> list[tuple[str, str, str]]:
    """全局指令索引：[(指令, 完整原文, 插件展示名)]。"""
    index: list[tuple[str, str, str]] = []
    for p in plugins:
        title = _plugin_title(p)
        for cmd, raw in _plugin_cmds(p):
            index.append((cmd, raw, title))
    return index


def _match_plugin(plugins: list, query: str):
    """按序号 / 包名 / 展示名 / 关键字匹配插件。"""
    if query.isdigit():
        idx = int(query) - 1
        if 0 <= idx < len(plugins):
            return plugins[idx]
    q = query.lower()
    for p in plugins:
        if q == p.name.lower() or q == _plugin_title(p).lower():
            return p
    for p in plugins:
        if q in p.name.lower() or q in _plugin_title(p).lower():
            return p
    return None


def _find_command(index: list[tuple[str, str, str]], query: str):
    """按指令名查找。

    支持这些写法互相匹配：
      "签到" / "/签到"        → 命中「签到」
      "购买"                  → 命中「/购买 <编号>」（去掉参数后比较）
      "禁言"                  → 命中「/禁言 @用户 [时长]」
    """
    q = query.strip().lower()
    q_noslash = q[1:] if q.startswith("/") else q

    def head(cmd: str) -> str:
        """去掉参数部分，只留指令名。"""
        return cmd.split()[0].lstrip("/").lower()

    # 1) 精确匹配完整指令（含参数）
    for cmd, raw, title in index:
        if cmd.lower() in (q, "/" + q_noslash):
            return cmd, raw, title
    # 2) 精确匹配指令名（忽略参数与斜杠）
    for cmd, raw, title in index:
        if head(cmd) == q_noslash:
            return cmd, raw, title
    # 3) 模糊：原文里包含查询词
    for cmd, raw, title in index:
        if q_noslash in raw.lower():
            return cmd, raw, title
    return None


def _split_cmd_raw(cmd: str, raw: str) -> tuple[str, str]:
    """把 usage 原文拆成 (指令含参数, 说明)，仅用于列表对齐展示。

    "/购买 <编号>      购买鱼饵或升级鱼竿" -> ("/购买 <编号>", "购买鱼饵或升级鱼竿")
    "签到            每日签到领荣耀"       -> ("签到", "每日签到领荣耀")
    兜底路径下 cmd 可能只是行内片段（如 "/bilibili"），
    此时若 raw 以 cmd 开头则按列拆分，否则直接原文返回。
    """
    if not raw.startswith(cmd):
        return cmd, raw

    rest = raw[len(cmd):].strip()
    if not rest:
        return cmd, ""
    # 连续 2 个以上空格视为列分隔符
    parts = re.split(r"\s{2,}", rest, maxsplit=1)
    if len(parts) == 2:
        return f"{cmd} {parts[0]}".strip(), parts[1].strip()
    # 单空格分隔：把开头连续的 <xxx> 参数并回指令名
    m = re.match(r"^((?:<[^>]*>\s*)+)(.+)$", rest)
    if m:
        return f"{cmd} {m.group(1).strip()}".strip(), m.group(2).strip()
    return cmd, rest


def _disp_width(s: str) -> int:
    """字符串显示宽度：CJK 全角字符按 2 计，ASCII 按 1 计。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    """按显示宽度右侧补空格（QQ 里中文占两格，用 len() 对齐会错位）。"""
    return s + " " * max(0, width - _disp_width(s))


def _truncate(s: str, width: int) -> str:
    """按显示宽度截断，超出加省略号。"""
    if _disp_width(s) <= width:
        return s
    out, w = [], 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if w + cw > width - 1:
            break
        out.append(ch)
        w += cw
    return "".join(out) + "…"


@help_cmd.handle()
async def handle_help(event: MessageEvent, arg: Message = CommandArg()) -> None:
    """帮助菜单。

    设计原则：**用户要的是「我能发什么指令」，不是「装了哪些插件」**。
      - 不带参数：按插件分组列出所有指令 + 一行说明
      - 带参数：先按指令名查（/指令 签到），再按插件查（/指令 捕鱼）
    """
    plugins = _visible_plugins()
    index = _cmd_index(plugins)
    query = arg.extract_plain_text().strip()
    prefix = _reply_prefix(event)

    # ---------- 列表：列出所有指令 ----------
    if not query:
        limit = plugin_config.qqbot_toolbox_help_max
        lines = ["📖 我能做什么", "────────────"]
        shown = 0
        total_groups = sum(1 for p in plugins if _plugin_cmds(p))
        for p in plugins:
            cmds = _plugin_cmds(p)
            if not cmds:
                continue
            if shown >= limit:
                lines.append(f"…还有 {total_groups - shown} 组未显示，发 /help 看完整图")
                break
            shown += 1
            desc = (getattr(p.metadata, "description", "") or "").strip()
            lines.append(f"【{_plugin_title(p)}】{desc}")
            for cmd, raw in cmds:
                cmd_full, desc_text = _split_cmd_raw(cmd, raw)
                cmd_show = _truncate(cmd_full, 18)
                desc_show = _truncate(desc_text, 30) if desc_text else ""
                if desc_show:
                    lines.append(f"  {_pad(cmd_show, 20)}{desc_show}")
                else:
                    lines.append(f"  {cmd_show}")
        lines += [
            "────────────",
            "发 /指令 <指令名> 看详情，如：/指令 签到",
            "发 /help 看图片版完整菜单",
        ]
        await help_cmd.finish(prefix + "\n".join(lines))

    # ---------- 详情：先查指令 ----------
    hit = _find_command(index, query)
    if hit is not None:
        cmd, raw, title = hit
        lines = [f"📖 {cmd}", "────────────", raw, f"所属：{title}"]
        await help_cmd.finish(prefix + "\n".join(lines))

    # ---------- 详情：再查插件 ----------
    p = _match_plugin(plugins, query)
    if p is None:
        await help_cmd.finish(
            prefix + f"没找到「{query}」。发 /指令 看全部指令，或 /help 看图片菜单。"
        )

    meta = getattr(p, "metadata", None)
    lines = [f"📖 {_plugin_title(p)}"]
    if meta and meta.description:
        lines.append(meta.description)
    body = _usage_lines(p)
    if body:
        lines.append("────────────")
        lines.extend(body)
    else:
        lines.append("（该插件未提供用法说明）")
    await help_cmd.finish(prefix + "\n".join(lines))


# ---------------------------------------------------------------- /status
status_cmd = on_command("status", aliases={"状态"}, priority=5, block=True)


def _duration(seconds: float) -> str:
    """秒数转 '3天4小时25分'。"""
    seconds = int(seconds)
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds}秒"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


@status_cmd.handle()
async def handle_status(bot: Bot, event: MessageEvent) -> None:
    """查询运行状态：消息收发统计、已运行时长。

    刻意不显示 online/good：
      - 能回消息本身就证明在线，写「在线 ✅」是废话
      - good 是内部健康标记，对用户无意义
    也不显示 last_message_time：LLBot 统计的是全局最后一条消息，
      而用户刚发的这条 /status 指令本身就会把它刷新成「0 秒前」，
      永远是 0，没有信息量。
    """
    prefix = _reply_prefix(event)
    try:
        info = await bot.call_api(
            "get_status", _timeout=plugin_config.qqbot_toolbox_status_timeout
        )
    except Exception as e:  # noqa: BLE001 —— 状态查询失败不应中断回复
        await status_cmd.finish(prefix + f"⚠️ 状态查询失败：{type(e).__name__}: {e}")

    stat = info.get("stat") or {}
    lines = [f"🤖 [{bot.self_id}]"]
    if startup := stat.get("startup_time"):
        lines.append(f"  已连续运行 {_duration(time.time() - startup)}")
    recv = stat.get("message_received")
    sent = stat.get("message_sent")
    if recv is not None:
        lines.append(f"  累计收消息 {recv:,}")
    if sent is not None:
        lines.append(f"  累计发消息 {sent:,}")
    if recv:
        lines.append(f"  平均每 {recv // max(sent or 1, 1):,} 条消息回 1 次")
    await status_cmd.finish(prefix + "\n".join(lines))

"""待办插件的纯逻辑：解析、格式化、导出。

本模块不依赖 Bot 连接与存储，可单独导入做单元测试。

两类解析：

``parse_quick(text)``
    对话式录入「我要做 给群管插件加个冷却」。支持版本控制风格的标记：
    ``!紧急`` / ``!!高`` / ``~低``（``!!!最高`` 也行），``#标签`` 可多个。

``parse_command(sub, rest)``
    ``/待办 <子命令> [参数]`` 的解析，返回 ``Action``。

``parse_when(text)``
    提醒时间：``18:00`` / ``明天 9 点`` / ``后天10点`` / ``3天后`` / ``2小时后`` /
    ``2026-11-11 20:00`` / ``今天``（= 当天 20:00）。
"""

import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .action_parse import Action, MAIN_ALIASES, STATUS_WORDS, parse_command  # noqa: F401

# 优先级：1 紧急 / 2 高 / 3 中 / 4 低
PRIORITY_LABELS = {1: "紧急", 2: "高", 3: "中", 4: "低"}
PRIORITY_MARKS = {1: "‼️", 2: "❗", 3: "▪️", 4: "▫️"}

TITLE_MAX = 200
NOTE_MAX = 1000
TAGS_MAX = 5
TIMEOUT = 30 * 86400  # 单条待办自建/变更后，最多多少秒内算「新鲜」而被导出给 DSH
_TIME_HINT = "时间看不懂。可以写：18:00、明天 9 点、后天下午3点、3天后、2小时后、2026-11-11 20:00"


# ---------------------------------------------------------------- 主/次标题
_CJK_CHARS = r"\u3400-\u9fff\uff08\uff09\u3001\uff0c\u3002"


def normalize_spaces(text: str) -> str:
    """压缩多余空白：连续空格 / 全角空格 -> 单个空格。

    注意：**不要**在这里合并汉字之间的空格。客户端可能把「完成 周报」的字段分隔
    也插入成「完成 周 报」，汉字间合并会把字段粘死；字间空格在解析阶段
    按「已知命令词 / 编号」边界处理（见 ``parse_command`` 与 ``_after_command``）。
    """
    text = re.sub(r"[\s\u3000]+", " ", text or "").strip()
    return text


_HEAD_TAIL_RE = re.compile(r"^(?P<head>\S+?|我)(?P<tail>\s*[#＃]?\d.*)$")


def split_head(words: list[str]) -> tuple[str, str]:
    """``['<main>', '<sub>']`` -> ``(main, sub)``；只有一个时 sub 为空。

    客户端常把「完成 3」的字间空格删掉变成「完成3」或「完成3天 后 9点」，
    这里把开头的命令词和紧跟着的编号 / 内容拆开。
    """
    if not words:
        return "", ""
    first = words[0]
    rest = " ".join(words[1:])
    # 「3天后」这种中文时长不是编号，保持原样
    m = _HEAD_TAIL_RE.match(first)
    if m and not re.match(r"^\d+\s*[天日小时钟头分钟週周]", m.group("tail").strip()):
        head, tail = m.group("head"), m.group("tail").strip()
        return head, (tail + (" " + rest if rest else "")).strip()
    return first, rest


MAIN_ALIASES = {
    "做": "add", "新建": "add", "添加": "add", "记": "add",
    "列表": "list", "查看": "list",
    "完成": "done", "做完": "done", "搞定": "done", "我做完了": "done", "完成了": "done",
    "别完成了": "done", "别做完了": "done",
    "删除": "del", "删掉": "del", "移除": "del", "删": "del",
    "放弃": "drop", "不做了": "drop", "搁置": "drop", "取消": "drop",
    "开始": "start", "进行中": "start", "做中": "start",
    "恢复": "start", "重开": "start",
    "改": "edit", "修改": "edit", "重命名": "edit", "改名": "edit",
    "优先级": "prio", "备注": "note", "标签": "tag", "提醒": "remind", "记得": "remind",
    "导出": "export", "同步": "export", "安排": "export",
    "安排下去": "export", "交给": "export",
    "交接": "title", "标题": "title", "交接标题": "title", "任务名": "title",
    "清理": "del", "清空完成": "del", "清空": "del",
    "帮助": "help", "help": "help", "说明": "help",
}

# 没写状态标记、也没写子命令时，按动作词推断
STATUS_WORDS = {
    "完成": "done", "做完了": "done", "做完": "done", "搞定": "done", "done": "done",
    "放弃": "drop", "不做了": "drop", "搁置": "drop", "drop": "drop",
    "开始": "doing", "进行中": "doing", "做中": "doing", "start": "doing",
    "恢复": "open", "重开": "open", "reopen": "open",
}


# ---------------------------------------------------------------- 对话式录入
_MARK_RE = re.compile(r"^(?P<marks>[!~]{1,3})(?P<rest>.*)$", re.S)


def _priority_from_marks(marks: str) -> int:
    """``!`` = 紧急(1)，``!!`` / ``!!!`` = 高(2)，``~`` = 低(4)。"""
    if "~" in marks:
        return 4
    return 2 if len(marks) >= 2 else 1


def parse_quick(text: str) -> Optional[dict[str, Any]]:
    """把「自然语言」的待办解析成 ``{title, priority, tags}``；没有标题返回 ``None``。

    >>> parse_quick("!!修复 /烟 的冷却 #群管")
    {'title': '修复 /烟 的冷却', 'priority': 2, 'tags': ['群管']}
    """
    text = (text or "").strip()
    if not text:
        return None
    # 「#标签」抽出（# 后面不能是空白）
    tags = re.findall(r"#(?P<tag>[^\s#]+)", text)
    text = re.sub(r"#\S+", " ", text).strip()
    priority = 3
    m = _MARK_RE.match(text)
    if m:
        priority = _priority_from_marks(m.group("marks"))
        text = m.group("rest").strip()
    # 额外的「!紧急」「!高」「!低」写法
    for word, value in (("紧急", 1), ("最高", 1), ("高", 2), ("低", 4), ("不急", 4)):
        pat = re.compile(rf"^{re.escape(word)}\s+")
        if pat.match(text):
            priority = value
            text = pat.sub("", text, count=1).strip()
            break
    text = re.sub(r"\s+", " ", text).strip(" ，。,.；;")
    if not text:
        return None
    return {
        "title": text[:TITLE_MAX],
        "priority": priority,
        "tags": [t[:20] for t in dict.fromkeys(tags)][:TAGS_MAX],
    }


# ---------------------------------------------------------------- 时间
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def cn_to_int(text: str) -> Optional[int]:
    """「18」「十八」「二十」「三十」-> int；看不懂返回 None。"""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    m = re.fullmatch(r"(?P<tens>[一二两三四五六七八九])?十(?P<ones>[一二三四五六七八九])?", text)
    if m:
        tens = _CN_DIGITS.get(m.group("tens") or "一", 1)
        ones = _CN_DIGITS.get(m.group("ones") or "零", 0)
        return tens * 10 + ones
    return None


_TIME_RE = re.compile(
    r"(?:(?P<h>\d{1,2})\s*[:：]\s*(?P<m>\d{1,2})"
    r"|(?P<cnh>[零〇一二两三四五六七八九十]{1,3}|[01]?\d|2[0-3])\s*(?:点|时)\s*(?:(?P<half>半)|(?P<cnm>\d{1,2}|[零〇一二三四五六七八九十]{1,3})\s*分?)?)"
)
_DATE_RE = re.compile(r"(?P<y>\d{4})\s*[-/.年]\s*(?P<mo>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*日?")
_REL_RE = re.compile(r"(?<![\d月])(?P<n>\d{1,3})\s*(?P<unit>天|日|小时|钟头|分钟|週|周|星期)(?:后|之后|以后)?")
_WEEK_RE = re.compile(r"(?:下周|下个?星期)\s*(?P<wd>[一二三四五六日天])")
_DEFAULT_HOUR = 20  # 「明天」「3天后」不写具体时间时，默认当天 20:00
_CJK = r"[零〇一二两三四五六七八九十]"
# 钟点：18:00 / 9点 / 9点半 / 9点15 / 九点十五 / 下午3点（上午/下午/晚上/凌晨 前缀 + 1 小时偏移）
_TIME_RE = re.compile(
    r"(?P<ap>上午|早上|早晨|凌晨|中午|下午|傍晚|晚上|夜里|今晚|明晚)?\s*"
    r"(?:"
    r"(?P<h>\d{1,2})\s*[:：]\s*(?P<m>\d{1,2})"
    r"|(?P<cnh>[01]?\d|2[0-3]|" + _CJK + r"{1,3})\s*(?:点|时)\s*(?:(?P<half>半)|(?P<cnm>\d{1,2}|" + _CJK + r"{1,3})\s*分?)?"
    r")"
)
_AP_OFFSET = {"下午": 12, "傍晚": 12, "晚上": 12, "夜里": 12, "今晚": 12, "明晚": 12}


def _pick_clock(text: str) -> Optional[tuple[int, int]]:
    """从文本里取钟点 ``(hour, minute)``，带上午/下午时做 12 小时制换算。"""
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    if m.group("h") is not None:
        hour, minute = int(m.group("h")), int(m.group("m"))
    else:
        hour = cn_to_int(m.group("cnh"))
        if m.group("half"):
            minute = 30
        elif m.group("cnm"):
            minute = cn_to_int(m.group("cnm"))
        else:
            minute = 0
    if hour is None or minute is None:
        return None
    ap = m.group("ap") or ""
    if ap in _AP_OFFSET and hour < 12:
        hour += _AP_OFFSET[ap]
    elif ap in ("下午", "傍晚", "晚上", "夜里", "今晚", "明晚") and hour == 12:
        pass  # 12 点保持 12:00
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _apply_clock(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


def parse_when(text: str, now: Optional[datetime] = None) -> tuple[Optional[int], str]:
    """解析提醒时间，返回 ``(时间戳, 错误说明)``；成功时错误为空。

    解析顺序：具体日期 > 下周几 > 今天/明天/后天 > 相对时长 > 只有钟点。
    显式写了钟点就用钟点，否则默认当天 20:00。
    """
    now = now or datetime.now()
    text = (text or "").strip()
    if not text:
        return None, _TIME_HINT

    clock = _pick_clock(text)
    day: date | None = None

    # 1) 具体日期：2026-11-11 / 11月11日
    m = _DATE_RE.search(text)
    if m:
        try:
            day = date(int(m.group("y")), int(m.group("mo")), int(m.group("d")))
        except ValueError:
            return None, f"日期不合法：{m.group(0)}"
    else:
        m = re.search(r"(?<![\d:])(?P<mo>1[0-2]|0?[1-9])\s*月\s*(?P<d>3[01]|[12]\d|0?[1-9])\s*[日号]?", text)
        if m:
            try:
                day = date(now.year, int(m.group("mo")), int(m.group("d")))
            except ValueError:
                return None, f"日期不合法：{m.group(0)}"
            if day < now.date() - timedelta(days=1):  # 写的是今年已经过去的日期 -> 明年
                day = day.replace(year=day.year + 1)

    # 具体日期已确定：直接按时钟（没写钟点用默认 20:00）返回，
    # 不能让「11月11日」里的「11日」再被当成相对时长。
    if day is not None:
        hour, minute = clock if clock else (_DEFAULT_HOUR, 0)
        return int(_apply_clock(day, hour, minute).timestamp()), ""

    # 2) 下周几
    if day is None:
        m = _WEEK_RE.search(text)
        if m:
            wd = "一二三四五六日天".index(m.group("wd"))
            delta = (7 - now.weekday()) + wd  # 下周一 = 下周开始后的第 wd 天
            day = now.date() + timedelta(days=delta)

    # 3) 今天 / 明天 / 后天 / 大后天
    if day is None:
        offsets = (("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1), ("今晚", 0), ("明晚", 1), ("今天", 0), ("今日", 0))
        for word, days in offsets:
            if word in text:
                day = now.date() + timedelta(days=days)
                break

    # 4) 相对时长：2小时后 / 3天后
    m = _REL_RE.search(text)
    if m:
        n = int(m.group("n"))
        unit = m.group("unit")
        if n <= 0:
            return None, "时间必须大于 0"
        if unit == "分钟":
            return int((now + timedelta(minutes=n)).timestamp()), ""
        if unit in ("小时", "钟头"):
            return int((now + timedelta(hours=n)).timestamp()), ""
        base = _apply_clock(day or now.date(), _DEFAULT_HOUR, 0)
        target = base + timedelta(weeks=n) if unit in ("週", "周", "星期") else base + timedelta(days=n)
        if clock:
            target = target.replace(hour=clock[0], minute=clock[1])
        return int(target.timestamp()), ""

    if day is not None:
        hour, minute = clock if clock else (_DEFAULT_HOUR, 0)
        return int(_apply_clock(day, hour, minute).timestamp()), ""

    # 5) 只有钟点：今天，已过则明天
    if clock:
        base = _apply_clock(now.date(), *clock)
        if base < now:
            base += timedelta(days=1)
        return int(base.timestamp()), ""

    return None, _TIME_HINT


# ---------------------------------------------------------------- 格式化
def fmt_time(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def fmt_date(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def fmt_relative(ts: int, now: Optional[datetime] = None) -> str:
    """``2小时后`` / ``3天前`` 这类相对说法。"""
    now = now or datetime.now()
    diff = datetime.fromtimestamp(ts) - now
    seconds = int(diff.total_seconds())
    future = seconds >= 0
    seconds = abs(seconds)
    if seconds < 3600:
        text = f"{max(1, seconds // 60)}分钟"
    elif seconds < 86400:
        text = f"{seconds // 3600}小时"
    else:
        text = f"{seconds // 86400}天"
    return f"{text}后" if future else f"{text}前"


# 超过这么多天没动，视为「搁置中」
STALE_DAYS = 7


def age_days(task: dict[str, Any], now: float | None = None) -> int:
    """距上次变更多久（天）；新建 / 有历史动作都算变更。"""
    now = time.time() if now is None else now
    stamps = [int(task.get("created_at") or 0)]
    stamps += [int(h.get("at") or 0) for h in task.get("history") or []]
    last = max(stamps) if stamps else 0
    return max(0, int((now - last) // 86400))


def is_stale(task: dict[str, Any], now: float | None = None) -> bool:
    return task.get("status") in ("open", "doing") and age_days(task, now) >= STALE_DAYS


def line(task: dict[str, Any], show_id: bool = True, show_age: bool = True) -> str:
    """群聊里的一行显示。

    例：``❗ #3 修复梗别名 #群管 · 搁置 9天 ⏰10-21 15:10``
    """
    head = PRIORITY_MARKS.get(task.get("priority", 3), "▪️")
    status = task.get("status")
    if status == "doing":
        head = "🔄"
    elif status == "done":
        head = "✅"
    elif status == "dropped":
        head = "🚫"
    ident = f"#{task['id']}" if show_id else ""
    tags = "".join(f" #{t}" for t in task.get("tags") or [])
    title = task["title"]
    if status in ("done", "dropped"):
        title = f"~~{title}~~"
    remind = ""
    if task.get("remind_at") and status in ("open", "doing"):
        remind = f" ⏰{fmt_time(task['remind_at'])}"
    age = ""
    if show_age:
        days = age_days(task)
        if status in ("open", "doing"):
            if days >= STALE_DAYS:
                age = f" · 搁置{days}天"
            elif days >= 1:
                age = f" · {days}天"
    return f"{head} {ident} {title}{tags}{age}{remind}".replace("  ", " ").strip()


def list_text(tasks: list[dict[str, Any]], title: str = "📋 待办", empty: str = "没有待办。用「我要做 <任务>」添加。") -> str:
    if not tasks:
        return empty
    return title + "\n" + "\n".join(line(t) for t in tasks)


# ---------------------------------------------------------------- 导出给 DSH
def export_markdown(tasks: list[dict[str, Any]], title: str = "") -> str:
    """把待办渲染成 Markdown 勾选清单（DSH 能直接解析 ``- [ ] #123 任务``）。"""
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    if not tasks:
        lines.append("_没有待办的活。_")
        return "\n".join(lines) + "\n"
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for prio in (1, 2, 3, 4):
        items = [t for t in tasks if t["priority"] == prio and t["status"] == "open"]
        if items:
            groups.append((f"{PRIORITY_MARKS[prio]} {PRIORITY_LABELS[prio]}", items))
    for status, label in (("doing", "🔄 进行中"), ("done", "✅ 已完成")):
        items = [t for t in tasks if t["status"] == status]
        if items:
            groups.append((label, items))
    for head, items in groups:
        lines.append(f"## {head}")
        for t in items:
            box = "x" if t["status"] == "done" else " "
            meta = []
            if t.get("tags"):
                meta.append(" ".join(f"`#{tag}`" for tag in t["tags"]))
            if t.get("created_at"):
                meta.append(fmt_date(t["created_at"]))
            if t.get("remind_at"):
                meta.append(f"提醒 {fmt_time(t['remind_at'])}")
            suffix = f"  <!-- {' · '.join(meta)} -->" if meta else ""
            note = f"\n    > {t['note']}" if t.get("note") else ""
            lines.append(f"- [{box}] #{t['id']} {t['title']}{suffix}{note}")
        lines.append("")
    lines.append(f"_导出时间：{datetime.now():%Y-%m-%d %H:%M}，共 {len(tasks)} 条_")
    return "\n".join(lines).rstrip() + "\n"


def export_json(
    tasks: list[dict[str, Any]],
    title: str = "",
    filter_mode: str = "active",
    header_level: int = 2,
) -> dict[str, Any]:
    """JSON 格式：字段齐全，适合脚本/DSH 直接读。"""
    return {
        "source": "qqbot_todo",
        "exported_at": int(time.time()),
        "title": title,
        "filter": filter_mode,
        "header_level": header_level,
        "count": len(tasks),
        "tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "status": t["status"],
                "priority": t["priority"],
                "priority_label": PRIORITY_LABELS.get(t["priority"], "中"),
                "tags": t.get("tags") or [],
                "note": t.get("note") or "",
                "created_at": fmt_date(t.get("created_at")),
                "done_at": fmt_date(t.get("done_at")) if t.get("done_at") else "",
                "remind_at": fmt_time(t.get("remind_at")) if t.get("remind_at") else "",
                "dsh_task_id": t.get("dsh_task_id") or "",
            }
            for t in tasks
        ],
    }


def is_fresh(task: dict[str, Any], now: Optional[float] = None) -> bool:
    """新建 / 变更在 TIMEOUT 内算「新鲜」，只有新鲜的才导出给 DSH，避免把陈年旧账全塞过去。"""
    now = time.time() if now is None else now
    stamps = [task.get("created_at") or 0]
    stamps += [int(h.get("at") or 0) for h in task.get("history") or []]
    return now - max(stamps) <= TIMEOUT


def summarize(tasks: list[dict[str, Any]]) -> str:
    """``待办 3 · 进行中 1 · 已完成 5``。"""
    counts = {"open": 0, "doing": 0, "done": 0, "dropped": 0}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return " · ".join(
        f"{label} {counts[key]}" for key, label in (("open", "待办"), ("doing", "进行中"), ("done", "已完成"), ("dropped", "已放弃"))
    )


def parse_id(text: str) -> Optional[int]:
    text = (text or "").strip().lstrip("#＃")
    return int(text) if text.isdigit() else None

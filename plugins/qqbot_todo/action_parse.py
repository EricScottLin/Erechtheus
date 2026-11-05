"""``/待办`` 子命令的解析（``/待办 <sub> <args...>`` -> :class:`Action`）。

单独放在一个模块里：``parse_command`` 会调用同一模块里的辅助函数，
避免重复执行 ``/待办 …`` 参数时出现名字解析歧义。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------- 主命令词
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

# 编号 / 内容粘在命令词后面的最简解法：认最常见的几种动作词
_GLUED_RE = re.compile(
    r"^(?P<head>完成|搞定|删除|删掉|放弃|开始|恢复|修改|改名|备注|标签|提醒|优先级|交接|导出)"
    r"(?P<tail>\s*[#＃]?\d.*)$"
)


@dataclass
class Action:
    """``/待办`` 子命令解析结果。"""

    name: str
    target: Optional[str] = None      # 目标（id 字符串，或「我」「全部」）
    text: str = ""                    # 标题 / 备注 / 新建时的任务名
    value: str = ""                   # 优先级、标签、时间、交接标题等
    error: str = ""                   # 参数错误时给用户的提示
    extra: dict[str, Any] = field(default_factory=dict)


def _split_head(words: list[str]) -> tuple[str, str]:
    """``['<main>', '<sub>']`` -> ``(main, sub)``；只有一个时 sub 为空。

    「完成3」「完成3天 后 9点」这种编号粘在命令词上的写法会被拆开；
    但「3天后」里的 3 是时长，不是编号，保持原样。
    """
    if not words:
        return "", ""
    first = words[0]
    rest = " ".join(words[1:])
    m = _GLUED_RE.match(first)
    if m:
        glued = m.group("tail").strip()
        # 只有「命令词 + 数字编号」才是粘连（完成3 / 改2），
        # 「改2号按钮」这种把编号当目标名一部分的写法不拆
        if re.match(r"^[#＃]?\d+(\s|$)", glued) and not re.match(r"^\d+\s*[天日小时钟头分钟週周]", glued):
            return m.group("head"), (glued + (" " + rest if rest else "")).strip()
    return first, rest


def _with_target(name: str, word: str, tail: str) -> Action:
    """``<动作> <目标> [参数...]``。目标缺省时按动作给默认值。"""
    target = word or {"done": "上次", "del": "上次", "drop": "上次", "start": "上次"}.get(name, "")
    if not word and name == "remind":
        return Action(name, target="", error="用法：/待办 提醒 <编号或关键词> <时间>")
    return Action(name, target=target, text=tail, value=tail)


def parse_command(sub: str, rest: list[str]) -> Action:
    """``/待办 <sub> <rest...>`` -> ``Action``。"""
    if not sub:
        return Action("list")
    name = MAIN_ALIASES.get(sub, "")
    if not name:
        return Action("help", error=f"不认识「{sub}」，发 /待办 看用法。")

    word, tail = _split_head(rest)
    # 命令词后面粘着文字（「完成3」经上面拆分后 first 仍是「完成」）时，把命令词本身去掉
    if name in ("done", "del", "drop", "start", "edit", "prio", "note", "tag", "remind", "record"):
        for alias, action_name in MAIN_ALIASES.items():
            if action_name == name and word == alias:
                first, _, rest_text = tail.partition(" ")
                word, tail = first, rest_text
                break

    if name in ("done", "del", "drop", "start", "edit", "prio", "note", "tag", "remind", "record"):
        # 「完成 3」「改 3 新标题」：第一个参数是目标
        if not word and name == "done":
            return Action("list", value="完成")
        if not word and name == "del":
            return Action(name, target="结束的全部")
        return _with_target(name, word, tail)

    # 新增 / 查看 / 导出 / 交接：第一个参数是任务名或模式
    return Action(name, value=word, extra={"rest": tail})

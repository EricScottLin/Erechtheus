"""命令纠错：用户发错命令时，找出最像的几条命令。

参考 thefuck 的思路：先算「编辑距离」再看「结构相似」，中文命令还要过一遍
**拼音层面**的距离（``shuoming`` -> ``说明``、``qiansao`` -> ``签到``）。

打分维度（分数越高越像）：

- 完全相等（含别名）-> 无穷大（直接跳过，说明命令是对的）
- 一个是另一个的前缀 / 包含关系 -> 加权
- 编辑距离（原始文本、去掉命令前缀后、拼音）
- 首字/首字母相同
- 长度接近

本模块不依赖 NoneBot，可单独导入做单元测试。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional

# 一条命令（可带别名与说明）
@dataclass(frozen=True)
class CommandEntry:
    name: str  # 主命令名，如「签到」
    aliases: tuple[str, ...] = ()  # 别名，如（「每日签到」,）
    description: str = ""  # 来自插件 usage 的一句话说明

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass
class Suggestion:
    entry: CommandEntry
    score: float
    matched: str  # 命中的是主名还是某个别名
    reason: str  # 给用户看的匹配原因（拼音/前缀/相近）


# ---------------------------------------------------------------- 拼音
_pinyin_cache: dict[str, str] = {}
_PINYIN_OK: Optional[bool] = None


def _pinyin_of(text: str) -> str:
    """取无声调拼音（lowercase，去掉非字母数字）。没有 pypinyin 时返回空串。"""
    global _PINYIN_OK
    if text in _pinyin_cache:
        return _pinyin_cache[text]
    if _PINYIN_OK is False:
        return ""
    try:
        from pypinyin import Style, lazy_pinyin

        _PINYIN_OK = True
        # errors 默认会把非汉字原样返回；之前用 errors="ignore" 会把
        # 「qiansao」这类已经是拼音的输入整段吞掉，等于拼音匹配完全失效
        parts = lazy_pinyin(text, style=Style.NORMAL)
        result = "".join(str(p) for p in parts).lower()
        result = "".join(ch for ch in result if ch.isalnum())
    except Exception:  # noqa: BLE001 —— 没装 pypinyin 时退化为纯文本匹配
        _PINYIN_OK = False
        result = ""
    _pinyin_cache[text] = result
    return result


def pinyin_available() -> bool:
    """当前环境能否做拼音纠错。"""
    _pinyin_of("测试")
    return bool(_PINYIN_OK)


# ---------------------------------------------------------------- 距离
def levenshtein(a: str, b: str) -> int:
    """经典编辑距离（插入/删除/替换各计 1）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def _ratio(a: str, b: str) -> float:
    """0~1 的相似度。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _core(raw: str, command_starts: Iterable[str]) -> str:
    """去掉命令前缀（``/`` 等）、开头的 @与唤醒词残留、空白，只留命令主体。"""
    text = raw.strip()
    for prefix in sorted(command_starts, key=len, reverse=True):
        if prefix and text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip()
    # 只取第一段（命令后面的参数不参与比对）
    return text.split()[0] if text.split() else ""


# ---------------------------------------------------------------- 打分
def score_one(query: str, candidate: str) -> tuple[float, str]:
    """单条候选的打分，返回 ``(分数, 原因)``。"""
    if not query or not candidate:
        return 0.0, ""
    if query == candidate:
        return float("inf"), "完全一致"

    q, c = query.lower(), candidate.lower()
    score = 0.0
    reason = ""

    # 1. 前缀 / 包含（「签到」 vs 「签到啦」，「金句」 vs 「金句添加」）
    if c.startswith(q) or q.startswith(c):
        shorter = min(len(q), len(c))
        longer = max(len(q), len(c))
        score += 40 + 20 * (shorter / longer)
        reason = "前缀相同"
    elif q in c or c in q:
        score += 25 + 15 * (min(len(q), len(c)) / max(len(q), len(c)))
        reason = "包含相同文字"

    # 2. 文本相似度（长度差太多时不可靠：「ban」和「yan」相似度 0.67 却没意义）
    #    如果输入本身是拼音/英文，文本相似度会被拼音维度重复计算，这里降权
    qp_raw, cp_raw = _pinyin_of(q), _pinyin_of(c)
    input_is_pinyin = bool(qp_raw) and bool(cp_raw) and q.isascii()
    ratio = _ratio(q, c)
    weight = 45 if min(len(q), len(c)) >= 3 else 20
    if input_is_pinyin:
        weight *= 0.5
    score += ratio * weight
    if ratio >= 0.5 and not reason:
        reason = "文字相近"

    # 3. 编辑距离（短命令容错更严格）
    dist = levenshtein(q, c)
    # 很短的输入（1~2 个字）编辑距离参考价值低：「骂」和「烟」差一个字但没有意义
    if min(len(q), len(c)) >= 2:
        if dist == 1:
            score += 35
            reason = reason or "只差一个字"
        elif dist == 2:
            score += 18
        elif dist >= 4:
            score -= dist * 3
    elif dist >= 2:
        score -= 12

    # 4. 拼音层面（中文命令的核心）：整体拼音距离 + 前缀
    qp, cp = qp_raw, cp_raw
    if qp and cp:
        if qp == cp:
            # 拼音完全一致是最强信号（yan -> 烟），要压过「ban」这种文本相似
            score += 200
            reason = f"拼音一致（{qp}）"
        else:
            pd = levenshtein(qp, cp)
            # 用较短的长度衡量置信度：yan(3) vs ban(3) 差 1 个字母不算像
            plen = max(min(len(qp), len(cp)), 1)
            # 拼音越长，差 1 个字母越可信；2 字母的 ban/yan 差 1 个不算太像
            confidence = min(plen, 8) / 8
            if pd <= 1:
                score += 40 + 45 * confidence
                reason = f"拼音相近（{cp}）"
            elif pd <= 2 and plen >= 4:
                score += 25 + 25 * confidence
                reason = reason or f"拼音相近（{cp}）"
            elif pd <= 3 and plen >= 6:
                score += 10
            # 拼音前缀，如 qian -> qiansao
            if cp.startswith(qp) or qp.startswith(cp):
                score += 20
                reason = reason or f"拼音开头相同（{cp}）"
            if ratio < 0.3 and pd >= 4:
                score -= 10

    # 5. 首字母相同（qsc -> 签到）
    if qp and cp and qp[0] == cp[0]:
        score += 6

    # 6. 长度接近的弱加分，避免短的命令什么都像
    if abs(len(c) - len(q)) <= 1:
        score += 4

    return score, reason


def suggest(
    query: str,
    commands: Iterable[CommandEntry],
    limit: int = 4,
    threshold: float = 45.0,
) -> list[Suggestion]:
    """返回最相似的若干条命令，按分数降序。

    阈值默认 45，低于它的不再推荐（宁可不提示，也别给一堆无关命令）。
    """
    if not query:
        return []
    results: list[Suggestion] = []
    for entry in commands:
        best: Optional[Suggestion] = None
        for name in entry.all_names:
            score, reason = score_one(query, name)
            if score == float("inf"):
                return []  # 命令本身是对的，不需要纠错
            if best is None or score > best.score:
                best = Suggestion(entry=entry, score=score, matched=name, reason=reason)
        if best is not None and best.score >= threshold:
            results.append(best)

    results.sort(key=lambda s: (-s.score, len(s.matched), s.entry.name))
    # 同一条命令只保留一个别名命中
    seen: set[str] = set()
    out: list[Suggestion] = []
    for item in results:
        if item.entry.name in seen:
            continue
        seen.add(item.entry.name)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def format_suggestions(
    query: str,
    suggestions: list[Suggestion],
    command_start: str = "/",
) -> str:
    """把纠错结果排版成给群友看的文本（已过滤 Markdown，适配 QQ）。"""
    if not suggestions:
        return (
            f"没找到命令「{query}」。\n"
            f"发「{command_start}指令」看看全部命令吧。"
        )
    lines = [f"没有「{query}」这个命令，你是不是想用："]
    for i, s in enumerate(suggestions, 1):
        alias = ""
        if s.matched != s.entry.name:
            alias = f"（别名：{s.matched}）"
        else:
            others = [a for a in s.entry.aliases if a != s.matched]
            if others:
                alias = f"（也写作：{'、'.join(others[:2])}）"
        desc = f" - {s.entry.description}" if s.entry.description else ""
        lines.append(f"{i}. {command_start}{s.entry.name}{alias}{desc}")
    lines.append(f"都不对的话，发「{command_start}指令」看全部命令。")
    return "\n".join(lines)


def extract_query_from_usage(usage: str) -> list[str]:
    """从插件 usage 文本里解析出它提到的命令（``/xxx`` 形式）。"""
    import re

    found = re.findall(r"[\/／]([A-Za-z\u4e00-\u9fff_][A-Za-z0-9\u4e00-\u9fff_]*)", usage or "")
    return found

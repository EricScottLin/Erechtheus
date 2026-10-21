"""文本处理纯逻辑：输入压缩、输出过滤与字数限制。

本模块不依赖 NoneBot / 网络，可单独导入做单元测试。
"""

import re
import unicodedata
from typing import Iterable, Optional

# 零宽字符与常见不可见控制字符（保留 \n \t）
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# C0/C1 控制字符（\n \t 之外）一律删掉
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")
_SPACE_RUN_RE = re.compile(r"[ \t\u00a0\u3000]+")
_NEWLINE_RUN_RE = re.compile(r"\n{3,}")
# 反复出现的「哈哈哈」「233」「。。。」压缩成一次
# 注意：别把数字写进这个通配的分支里，否则会把「233333」整段吃成一个 token
_LAUGH_RUN_RE = re.compile(r"(哈{2,}|嘿{2,}|呵{2,}|(?:。|\.|！|!|~|～){2,})")
_NUMBER_LAUGH_RE = re.compile(r"2333+")

# 截断标记（不计入字数上限）
ELLIPSIS = "…"


def _strip_invisible(text: str) -> str:
    return _CONTROL_RE.sub("", _ZERO_WIDTH_RE.sub("", text))


def fold_whitespace(text: str) -> str:
    """折叠半角/全角空格与连续换行，去掉零宽字符。"""
    text = _strip_invisible(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", " ")
    lines = [_SPACE_RUN_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _NEWLINE_RUN_RE.sub("\n\n", text).strip()


def compress_laughter(text: str, max_run: int = 2) -> str:
    """把「哈哈哈哈哈哈」压成「哈哈」这类重复语气词，减少无效 token。"""
    max_run = max(1, int(max_run))
    text = _LAUGH_RUN_RE.sub(lambda m: m.group(0)[0] * max_run, text)
    # 「233333」这类数字语气词单独处理，别塞进上面的通配分支
    return _NUMBER_LAUGH_RE.sub("233", text)


def normalize_text(text: str, fold: bool = True) -> str:
    """统一 NFKC（全角转半角、兼容字符归一），可选折叠空白。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _strip_invisible(text)
    return fold_whitespace(text) if fold else text


def strip_command_prefix(text: str, prefixes: Iterable[str]) -> str:
    """去掉开头的命令前缀（如 ``/``），只去一次。"""
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix and text.startswith(prefix):
            return text[len(prefix):].lstrip()
    return text


def cut_chars(text: str, limit: int, ellipsis: str = ELLIPSIS) -> str:
    """把文本裁剪到 ``limit`` 个字符，超出时加省略号（省略号不占额度）。"""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + ellipsis


def compress_input(
    text: str,
    limit: int,
    fold: bool = True,
    drop_urls_beyond: int = 0,
) -> str:
    """输入压缩：归一化、折叠空白、压缩语气词，最后按字数截断。

    Args:
        text: 原始提问文本
        limit: 最长字符数
        fold: 是否折叠空白
        drop_urls_beyond: 超过 N 个 URL 时丢弃多余链接（0 = 不限制）
    """
    text = normalize_text(text, fold=fold)
    text = compress_laughter(text)
    if drop_urls_beyond > 0:
        text = _limit_urls(text, drop_urls_beyond)
    return cut_chars(text, limit)


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _limit_urls(text: str, keep: int) -> str:
    seen = 0

    def _repl(m: re.Match) -> str:
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= keep else "[链接]"

    return _URL_RE.sub(_repl, text)


# ---------------------------------------------------------------- 输出过滤
# 常见违规内容粗筛（模型「越狱」时兜底；宁可偶尔误伤，也不放行）
_VIOLATION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(色情网站|成人视频|最新地址|无码视频|兽交|恋童).{0,10}(地址|链接|资源|下载)",
        r"(制造|制作|怎么造).{0,6}(炸弹|毒品|枪支)",
        r"自杀.{0,6}(方法|教程|怎么死)",
    )
]

# 模型偶尔会输出 Markdown / HTML，QQ 里不渲染，直接清掉
_MD_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
# 行首「- 」/「* 」列表符号（保留内容）
_MD_BULLET_RE = re.compile(r"^[ \t]*[-*+]\s+", re.MULTILINE)


def clean_markdown(text: str) -> str:
    """把 Markdown 降级成纯文本：去掉代码块围栏、图片、标题符、加粗星号。"""
    text = _MD_FENCE_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BOLD_RE.sub(r"\2", text)
    text = _MD_BULLET_RE.sub("", text)
    text = text.replace("`", "")
    return text


def find_forbidden(text: str, forbidden_words: Iterable[str]) -> Optional[str]:
    """返回命中的第一个违禁词；没有命中返回 ``None``。"""
    lowered = text.casefold()
    for word in forbidden_words:
        w = (word or "").strip()
        if w and w.casefold() in lowered:
            return w
    return None


def has_violation(text: str) -> bool:
    """粗筛色情/危险内容（关闭色情安全策略的模型才会走到这里）。"""
    return any(p.search(text) for p in _VIOLATION_PATTERNS)


def sanitize_output(
    text: str,
    max_chars: int,
    forbidden_words: Iterable[str] = (),
    fallback: str = "这个回答我不太方便说，换个问题吧。",
    fold: bool = True,
) -> str:
    """输出过滤：去 Markdown、折叠空白、违禁词/违规内容替换、按字数截断。

    命中违禁词或明显违规内容时**整段**替换为 ``fallback``，不做部分遮蔽，
    避免把敏感片段再回显给群里。
    """
    if not text:
        return fallback
    text = normalize_text(text, fold=fold)
    text = clean_markdown(text)
    text = compress_laughter(text)
    text = fold_whitespace(text)
    if not text:
        return fallback
    if find_forbidden(text, forbidden_words) or has_violation(text):
        return fallback
    return cut_chars(text, max_chars)


def wants_search(text: str) -> bool:
    """判断提问是否明显需要联网查资料（工具联不上时的兜底提示用）。"""
    if not text:
        return False
    if "是什么" in text or "是谁" in text or "什么是" in text:
        return True
    return bool(re.search(r"(最新|今天|现在|多少钱|汇率|天气|新闻|版本)", text))


def looks_like_abuse(text: str) -> bool:
    """极短的纯符号/纯 emoji 输入（如「？？？」）不值得调模型。"""
    stripped = re.sub(r"[\W_]+", "", text or "", flags=re.UNICODE)
    return len(stripped) == 0

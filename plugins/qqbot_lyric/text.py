"""文本归一化与歌词拼接的纯逻辑（不依赖 NoneBot / 网络，可单独单测）。

两件事：

1. :func:`normalize` —— 把一句聊天消息压成「可比较」的形式：
   NFKC、去空白标点、转小写。繁体原文保留一份，用于和繁体歌词比对。
2. :func:`build_candidates` —— 把同一个人连续发的几条消息拼成候选片段。
   很多人发歌词是一句一行连发，单看最后一条可能只有几个字，拼起来才像歌词。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

# 行首的歌词装饰：引号、破折号、项目符号、序号
_PAIRS = (
    ("「", "」"), ("『", "』"), ("《", "》"), ("〈", "〉"),
    ("\u201c", "\u201d"), ("\u2018", "\u2019"), ("(", ")"), ("（", "）"),
)
_LEADING_JUNK_RE = re.compile(
    r"^[\s\u3000\"'“”‘’「」『』《》〈〉()（）\[\]【】…—\-~～>*·•]+"
)
_TRAILING_JUNK_RE = re.compile(r"[\s\u3000…—\-~～>·•]+$")
# 纯「哈哈哈」「233」「顶」这类无信息消息
_NOISE_RE = re.compile(r"^(?:哈+|嘿+|呵+|嘻+|233+|顶+|马克+|插眼+|收)+$")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF\u3030\u303D\u3297\u3299]+"
)
_CMD_START = "/／!！.#%？?、"

# 「—— 歌名 - 歌手」这类署名行（bot 自己接龙时曾经带过，群友也会顺手带上）。
# 这种行不是歌词，**不能**拿去匹配，也不能当成「上一句」参与拼接。
_CREDIT_RE = re.compile(
    r"^(?:[-—–~～>*·•\s]+)?(?:"
    r"《[^》]{1,40}》|［[^］]{1,40}］|\[[^\]]{1,40}\]"
    r"|[^\n]{1,60}\s[-–—]\s[^\n]{1,40}"
    r")\s*$"
)
_LINE_SPLIT_RE = re.compile(r"[\r\n]+")


@dataclass
class Fragment:
    """一条可用于拼接的消息片段。"""

    text: str  # 清洗后的原文
    norm: str  # 归一化文本（比对用）
    user_id: str
    ts: float
    index: int = 0  # 在本段中的序号（0 = 最早）

    @property
    def chinese(self) -> bool:
        return has_chinese(self.text)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def _strip_paired_quotes(text: str) -> str:
    """「从前从前…」这类成对引号整体去掉（群友复制歌词常带引号）。"""
    changed = True
    while changed:
        changed = False
        text = text.strip()
        for left, right in _PAIRS:
            if text.startswith(left) and text.endswith(right) and len(text) > 2:
                text = text[len(left):-len(right)]
                changed = True
    return text


def clean_text(text: str) -> str:
    """去掉行首装饰、成对引号、收尾符号与 emoji，保留原文用词。"""
    text = _strip_paired_quotes(_strip_emoji(text or ""))
    text = _LEADING_JUNK_RE.sub("", text)
    text = _TRAILING_JUNK_RE.sub("", text)
    return " ".join(text.split())


def is_credit_line(text: str) -> bool:
    """这行是不是「—— 歌名 - 歌手」这类署名（不是歌词，不参与匹配）。"""
    body = clean_text(text)
    if not body or len(body) > 80:
        return False
    return bool(_CREDIT_RE.match(body))


def lyric_lines(text: str) -> list[str]:
    """把一条消息拆成「歌词行」：按换行拆开，去掉署名行。

    bot 自己接过龙之后，群里常有人把「下一句 +（—— 歌名 - 歌手）」整段复制回来；
    署名行混进去会让整段匹配失败（多出来的歌名把相似度拉低），所以先剔掉。
    """
    out: list[str] = []
    for raw in _LINE_SPLIT_RE.split(text or ""):
        line = clean_text(raw)
        if not line or is_credit_line(line):
            continue
        out.append(line)
    return out


def normalize(text: str, simplify: bool = True) -> str:
    """归一化：NFKC →（可选）繁体转简体 → 小写 → 只留中日韩文字与英数。

    歌词行和聊天消息都走这里，所以「我 听见 雨滴」和「我听见雨滴」能对上。
    **lrclib 上大量中文歌词是繁体**，所以歌词行要同时索引「原文」和「简体」两份
    （见 :func:`normalize_variants`），简体原文才能两种都命中。
    """
    if simplify:
        text = to_simplified(text)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    out = []
    for ch in text:
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
    return "".join(out)


_s2t = None
_t2s_available: Optional[bool] = None
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HANDLED = 0


def to_simplified(text: str) -> str:
    """繁体 → 简体（装了 opencc 精确转换；没装则原样返回 = 只认简体原文）。

    opencc 的 t2s 是「字级映射」，词级也基本正确（頭髮→头发）。
    """
    global _s2t, _t2s_available, _HANDLED
    if _t2s_available is None:
        try:
            from opencc import OpenCC

            _s2t = OpenCC("t2s")
            _t2s_available = True
        except Exception:  # noqa: BLE001 —— 没装 opencc 就原样返回
            _t2s_available = False
    if not _t2s_available or not text:
        return text
    han = _HAN_RE.findall(text)
    _HANDLED += len(han)
    if len(han) < 2:
        return text
    try:
        return _s2t.convert(text)
    except Exception:  # noqa: BLE001 —— 转换失败不能影响主流程
        return text


def to_traditional(text: str) -> str:
    """简体 → 繁体（用来给「繁体歌词」补一条简体的比对形态）。"""
    global _s2t_t
    if not text:
        return text
    if not to_simplified_available():
        return text
    try:
        from opencc import OpenCC

        if _s2t_t is None:
            _s2t_t = OpenCC("s2t")
        return _s2t_t.convert(text)
    except Exception:  # noqa: BLE001
        return text


_s2t_t = None


def to_simplified_available() -> bool:
    to_simplified("测试")  # 触发惰性探测
    return bool(_t2s_available)


def normalize_variants(text: str) -> list[str]:
    """一行文本可能的归一形态：简体原文 + （若本就是繁体）转成的简体。"""
    forms = [normalize(text, simplify=False)]
    converted = normalize(text, simplify=True)
    if converted and converted not in forms:
        forms.append(converted)
    return forms


def has_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def is_noise(text: str) -> bool:
    """纯语气词 / 纯符号 / 命令，不值得判定。"""
    raw = (text or "").strip()
    if not raw:
        return True
    if raw[0] in _CMD_START:
        return True
    if _NOISE_RE.match(normalize(raw)):
        return True
    return not normalize(raw)


# 代词：两条消息首句都以「我/你」这类代词开头时，通常不是同一段歌词
_PRONOUNS = ("我", "你", "他", "她", "它")


def _same_voice(prev: "Fragment", cur: "Fragment") -> bool:
    return bool(cur.text) and bool(prev.text) and cur.text[0] in _PRONOUNS and prev.text[0] in _PRONOUNS


def build_candidates(
    fragments: Iterable[Fragment],
    max_segments: int = 4,
    burst_window: float = 12.0,
    cross_user_window: float = 0.0,
) -> list[list[Fragment]]:
    """把碎片拼成候选组合：``[最新1条]``、``[最新2条]`` ... 上限 ``max_segments``。

    只拼接「相邻 + 时间间隔不超过窗口」的碎片（``fragments`` 需按时间正序）；
    两条都以「我/你」开头时视为不同的话，不拼。

    - 同一个人连发：窗口 = ``burst_window``（默认 12 秒）；
    - **不同人对唱**：窗口 = ``cross_user_window``（默认 0 = 不拼）。
      连续接龙时群友常常一句一句轮流发，这时要拼上一条才能定位到「下一句」；
      平时不拼，免得把两个人的闲聊拼成一段假歌词。

    返回的组合按长度从短到长排列，且**全部以最后一条结尾**。
    """
    items = list(fragments)
    if not items:
        return []
    groups: list[list[Fragment]] = []
    current: list[Fragment] = [items[0]]
    for prev, cur in zip(items, items[1:]):
        same_user = cur.user_id == prev.user_id
        limit = burst_window if same_user else max(0.0, cross_user_window)
        close = 0 <= (cur.ts - prev.ts) <= limit
        if close and not _same_voice(prev, cur):
            current.append(cur)
        else:
            groups.append(current)
            current = [cur]
    groups.append(current)

    # 候选必须以「当前这条」结尾：从最后一条往前取，而不是从最后一段的开头取
    # （最后一段只有一条时，旧写法会丢掉前面同一段里的上下文）
    tail = groups[-1]
    out: list[list[Fragment]] = []
    for size in range(1, min(max_segments, len(tail)) + 1):
        out.append(tail[-size:])
    return out


def join_fragments(frags: list[Fragment]) -> str:
    """拼接片段文本，换行分隔（给模型看时保留分行）。"""
    return "\n".join(f.text for f in frags if f.text)


def merged_norm(frags: list[Fragment]) -> str:
    return "".join(f.norm for f in frags)


def fragment_shape(raw: str, norm: str) -> Optional[str]:
    """本地版式判断：不像歌词就直接返回 ``None``（词表原因在 :mod:`gate` 里）。

    只看「外形」不看「词」——歌词是有一定规律的：

    - 太短（< 4 个有效字符）或太长（> 80）不像一句歌词；
    - 完整一句话（有句号/问号/感叹号收尾、且超过 18 字）更像聊天；
    - 纯链接 / 纯 @ / 纯命令。
    """
    if len(norm) < 4 or len(norm) > 80:
        return None
    stripped = raw.strip()
    if stripped.startswith(("http://", "https://", "www.")):
        return None
    if "](" in stripped or "@" in stripped:
        return None
    if len(norm) > 18 and stripped.endswith(("。", "？", "?", "！", "!", "…", "。。")):
        return None
    return "ok"


@dataclass
class GateVerdict:
    """本地版式闸门的结论。"""

    ok: bool
    reason: str = ""
    features: dict = field(default_factory=dict)

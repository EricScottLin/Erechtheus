"""拼音韵脚（纯逻辑，可单独单测）。

优先用 ``pypinyin`` 精确取韵母；没装时退回一张常用字韵母表 + 英文词尾规则，
保证插件在最小依赖下也能工作（只是押韵判断没那么准）。

归一原则：把韵母压成「粗韵」，只要听起来押韵就算（ang/eng/ing → a 这类），
因为群友发的歌词不一定逐字标准，宁可宽松一点。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import re
import unicodedata
from typing import Optional

# 粗韵归一表（顺序很重要：长韵母在前）
_FINALS = (
    ("iang", "a"), ("uang", "a"), ("ueng", "a"), ("iong", "e"), ("iang", "a"),
    ("ang", "a"), ("eng", "a"), ("ing", "a"), ("ong", "e"),
    ("ian", "e"), ("uan", "e"), ("van", "e"), ("van", "e"), ("üan", "e"),
    ("ao", "a"), ("iao", "a"), ("ai", "a"), ("uai", "a"), ("ia", "a"), ("ua", "a"),
    ("an", "e"), ("en", "e"), ("in", "e"), ("un", "e"), ("ün", "e"), ("vn", "e"),
    ("ou", "e"), ("iou", "e"), ("iu", "e"), ("ei", "e"), ("ui", "e"), ("uei", "e"),
    ("ie", "e"), ("üe", "e"), ("ve", "e"), ("uo", "o"), ("ao", "a"),
    ("a", "a"), ("o", "o"), ("e", "e"), ("i", "i"), ("u", "u"), ("v", "u"), ("ü", "u"),
)

# 兜底用的常用字韵母表（装在 pypinyin 时用不到，只是保证零依赖也能跑）
_FALLBACK_GROUPS = (
    ("a", "八 大 他 她 它 妈 花 家 发 达 沙 加 华 话 洒 怕 骂 答 那 拉 卡 马 法 塔"),
    ("o", "我 多 做 过 说 果 火 波 落 错 破 活 国 昨"),
    ("e", "的 了 呢 么 河 车 歌 着 这 个 色 得 热 客 特 和"),
    ("i", "你 里 起 是 一 之 日 子 事 己 几 知 时 意 记 弃 次 四 死 七 西 习 息 席 地 里"),
    ("u", "不 无 又 有 手 走 头 都 后 口 楼 秋 流 到 好 少 笑 老 靠 造 路 度"),
)
_FALLBACK_MAP: dict[str, str] = {}
for _rhyme, _chars in _FALLBACK_GROUPS:
    for _ch in _chars.split():
        _FALLBACK_MAP.setdefault(_ch, _rhyme)

# 英文词尾 -> 粗韵
_EN_FINALS = (
    ("ight", "a"), ("gn", "e"), ("ng", "a"), ("ay", "a"), ("ey", "e"), ("ow", "o"),
    ("ew", "u"), ("oo", "u"), ("ee", "i"), ("ea", "i"), ("ie", "i"), ("y", "i"),
    ("ar", "a"), ("or", "o"), ("er", "e"), ("an", "e"), ("en", "e"), ("ed", "e"),
    ("es", "e"), ("e", "e"), ("o", "o"), ("a", "a"), ("i", "i"),
)

_ASCII_WORD = re.compile(r"[a-zA-Z']+")
_PY_INITIAL = None
_PY_FAILED = False


def is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _ensure_pinyin() -> bool:
    """惰性 import pypinyin；返回是否可用。"""
    global _PY_INITIAL, _PY_FAILED
    if _PY_INITIAL is not None:
        return True
    if _PY_FAILED:
        return False
    try:
        from pypinyin import Style, lazy_pinyin

        _PY_INITIAL = (Style, lazy_pinyin)
        return True
    except Exception:  # noqa: BLE001 —— 没装就用兜底表
        _PY_FAILED = True
        return False


def pinyin_available() -> bool:
    return _ensure_pinyin()


def _cjk_final(ch: str) -> Optional[str]:
    """单字的粗韵；中文返回韵母粗韵，非中文返回 ``None``。"""
    if not is_cjk(ch):
        return None
    if _ensure_pinyin():
        style, lazy = _PY_INITIAL
        try:
            finals = lazy(ch, style=style.FINALS)
            final = str(finals[0]).lower() if finals else ""
        except Exception:  # noqa: BLE001
            final = ""
        if final:
            for suffix, rhyme in _FINALS:
                if final.endswith(suffix):
                    return rhyme
            return final
    return _FALLBACK_MAP.get(ch)


def _en_final(word: str) -> str:
    word = word.lower().strip("'")
    for suffix, rhyme in _EN_FINALS:
        if word.endswith(suffix):
            return rhyme
    return word[-1:] if word else ""


def rhyme_key(line: str) -> str:
    """一行的韵脚：中文取末字粗韵，英文取末词发音结尾。"""
    text = unicodedata.normalize("NFKC", (line or "").strip().lower())
    if not text:
        return ""
    if is_cjk(text[-1]):
        return _cjk_final(text[-1]) or ""
    words = _ASCII_WORD.findall(text)
    if words:
        return _en_final(words[-1])
    return ""


def rhyme_keys(lines: list[str]) -> list[str]:
    return [rhyme_key(line) for line in lines]


def same_rhyme(a: str, b: str) -> bool:
    return bool(a) and a == b

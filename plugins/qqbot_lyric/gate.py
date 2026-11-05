"""本地版式闸门：先用「歌词长什么样」的规律筛一遍，再决定要不要问模型。

歌词不是随便一句话。经验上歌词片段往往是：

- **很短**（一句 4~30 字），不以句号收尾；
- **分行**（多人发歌词常常一句一行、连发几条）；
- **没有话题化的口头语**（「咱们」「你们」+ 时间地点这类开会/吃饭的句子）；
- **有韵脚**（把整行近似拼音后，相邻行的结尾音相同/相近）；
- **有歌词语感词**（副歌高频的「我/你/想/爱/心/泪/夜……」密度高）；
- **英文也一样**（「if i had my life without you near me」）：是小写完整句子、
  带 few 个功能词（i/you/my/the/would）、没有问号、不是寒暄或问路。

这里只做**减分**，不做最终判定：分数太低的直接丢（省一次模型调用），
分数中上的交给模型确认。所有阈值都可以在 :mod:`config` 里调。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


from dataclasses import dataclass, field

from .rhyme import rhyme_key
from .text import Fragment

@dataclass
class Shape:
    """一段候选歌词的版式特征。"""

    text: str
    score: float = 0.5
    reasons: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def with_lines(self, lines: list[str]) -> "Shape":
        self.lines = lines
        return self


# 歌词语感高频词（以单字为主 —— 单字不容易误伤普通句子）
_SONG_WORDS = frozenset(
    "我你他她它爱想恋念情心思梦眼泪风月夜星海天云雨雪花火酒歌笑笑哭痛悲喜愁孤单寂寞温柔自由永远远方"
    "回忆从前曾经多少何时为谁为何心的歌温暖光芒世界时间岁日夜朝夕风雨尽头来去留走飞"
)
# 歌词特有的语气/转承字眼（聊天极少这样连用）
_LYRIC_FILLERS = frozenset("当初如今只好祭奠")
# 口语/事务性词汇：出现就说明更像聊天
_CHAT_WORDS = (
    "大家", "咱们", "你们", "几点", "什么时候", "记得", "别忘了", "开会", "下班", "上班",
    "工资", "报销", "收到", "在吗", "啥", "咋", "有没有", "是不是", "能不能",
    "请问", "稍等", "打卡", "签到", "作业", "考试", "上课", "快递", "外卖", "食堂",
    "你好", "在不在", "啥时候", "多少钱", "怎么", "为什么", "可以吗", "行不行", "咋整",
    "群主", "管理员", "禁言", "踢了", "进群", "出群", "群公告",
    # 语气助词收尾的对话特征（歌词极少用）
    "啊", "吧", "呀", "嘛", "哦", "啦",
)
# 明显的「点歌 / 问歌」意图：交给 /歌词 命令，不参与自然接龙
_TITLE_INTENT = ("唱首", "来首歌", "放首歌", "点歌", "这首歌", "那首歌", "歌名叫", "什么歌")

_QUESTION_TAIL = ("吗", "呢", "吧", "啊", "么", "嘛")
_PUNCT_END = "。！？!?…"

# ---------------------------------------------------------------- 英文
# 歌词里极常见的人称/功能词：出现越多越像歌词（日常群聊里也常见，所以权重不高）
# 只收「意境词」：人称/功能词（i/you/the/and）在英文歌词里也最多，
# 日常英文里同样满屏都是，放进来只会把 "I will send you the report" 判成歌词。
_EN_LYRIC_WORDS = frozenset(
    "love heart feel feeling dream dreams night tonight forever always alone wait "
    "remember reason believe without empty broken tears soul world life heaven "
    "baby yeah kiss eyes smile rain sun moon star fire touch hold near away gone "
    "keep fall breathe smile cry hurt lost fate light dark save wish".split()
)
# 英文寒暄 / 实用问句：命中优先当作聊天（hello 在歌里也常见，所以要「整句都像寒暄」才算）
# 寒暄里常见的虚词/程度词（判断「整句是不是寒暄」时不算内容）
_EN_FILLER = frozenset("a lot very much too also just really so well ok okay yes no sure".split())
_EN_GREETINGS = frozenset(
    "hello hi hey thanks thank please sorry bye goodbye good morning evening night "
    "how are you what who where when why which is are can could would should will "
    "do does did dont dont ve ll re m".split()
)
_EN_GREETING_PHRASES = (
    "how are you", "how's it going", "nice to meet you", "thank you", "thanks a lot",
    "thanks a bunch", "no problem", "that's fine", "sounds good", "got it", "excuse me",
    "good morning", "good night", "good evening", "see you", "what time",
    "where is", "how much", "what is", "who is", "can you", "could you",
    "do you have", "i need to", "i want to", "i think we", "we should",
)
# 英文里的聊天/工具语（开会、任务、链接、时间安排）
_EN_CHAT_WORDS = (
    "meeting", "deadline", "tomorrow", "today", "tonight", "email", "laptop", "password",
    "wifi", "project", "schedule", "report", "submit", "okay", "lgtm", "deploy", "build",
    "bug", "error", "download", "upload", "http", "www", "brb", "afaik", "imo",
    "failed", "fail", "works", "working", "fix", "fixed", "check", "checked", "send",
    "sent", "update", "updated", "done", "ready", "waiting", "wait", "ask", "asked",
    "guys", "please", "thanks", "thx", "morning", "afternoon", "evening",
)
_WORD_RE = None


def _words(text: str) -> list[str]:
    """英文分词（去标点、转小写）。"""
    import re

    global _WORD_RE
    if _WORD_RE is None:
        _WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z']*")
    return [w.lower().replace("\u2019", "'") for w in _WORD_RE.findall(text or "")]


def is_english(text: str, min_ratio: float = 0.8) -> bool:
    """整句是不是英文（字母占非空白字符的比例足够高）。"""
    compact = "".join(ch for ch in (text or "") if not ch.isspace())
    if len(compact) < 4:
        return False
    letters = sum(1 for ch in compact if ch.isascii() and ch.isalpha())
    return letters / len(compact) >= min_ratio


def analyze_english(text: str, explicit: bool = False) -> Shape:
    """英文歌词的版式打分（「像不像一句歌词」）。

    经验信号（都是英文歌词与日常英文的差别）：

    - 歌词几乎没有问号、很少感叹号；
    - 歌词是小写/句首大写 + 大量人称与功能词（I / you / my / the / would / without）；
    - 寒暄、问路、任务安排类句子（hello how are you / what time / meeting）更像聊天；
    - 太长的一段（> 90 词）多半是聊天或复制的资料。
    """
    shape = Shape(text=text)
    words = _words(text)
    n = len(words)
    score = 0.5
    reasons = shape.reasons
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    shape.lines = lines

    if n < 2:
        score -= 0.4
        reasons.append("太短-")
    elif n <= 18:
        score += 0.12
        reasons.append("词数像歌词+")
    elif n <= 30:
        score += 0.02
    else:
        score -= 0.35
        reasons.append("太长-")

    stripped = text.strip()
    if stripped.endswith("?"):
        score -= 0.3
        reasons.append("问号-")
    elif stripped.endswith(("!", ".")):
        score -= 0.1
        reasons.append("句末标点-")
    if stripped[:1].islower():
        score += 0.06
        reasons.append("小写开头+")
    if len(lines) >= 2:
        score += 0.1
        reasons.append("分行+")
        if lines[-1].endswith((",", "and", "the", "my", "your")):
            score += 0.05
            reasons.append("断句未完成+")

    lyric_hits = sum(1 for w in words if w in _EN_LYRIC_WORDS)
    density = lyric_hits / max(1, n)
    if density >= 0.35:
        score += 0.18
        reasons.append("歌词语感+")
    elif density >= 0.22:
        score += 0.08
    elif density < 0.1:
        score -= 0.1
        reasons.append("歌词语感-")

    low = stripped.lower()
    if any(p in low for p in _EN_GREETING_PHRASES):
        score -= 0.28
        reasons.append("寒暄/问句-")
    if any(w in _EN_CHAT_WORDS for w in words):
        score -= 0.15
        reasons.append("事务词-")
    # 整句几乎全是寒暄词（hello / thanks / how / are / you / a lot ...）
    content = [w for w in words if w not in _EN_GREETINGS and w not in _EN_FILLER]
    if n <= 8 and len(content) <= 1:
        score -= 0.3
        reasons.append("整句寒暄-")
    if any(ch.isdigit() for ch in text):
        score -= 0.12
        reasons.append("含数字-")

    if explicit:
        score += 0.2
        reasons.append("显式调用+")

    # 英文日常聊天比中文更容易撞上「像歌词」的外形，整体压一档；
    # 但压完仍 >=「obvious」才允许走免验证路径（见 relay），否则一律要模型确认。
    score -= 0.08
    reasons.append("英文降权-")
    shape.score = max(0.0, min(1.0, score))
    return shape


def apply_language_weight(shape: Shape) -> Shape:
    """英文片段的整体置信度略降（英文日常聊天比中文更容易撞上「像歌词」的外形）。

    实测英文命中极准（整句歌词搜歌名几乎必中），所以只降一点点，
    低分时就会落到「不能免验证」那一档。
    """
    if is_english(shape.text):
        shape.score = max(0.0, shape.score - 0.05)
        shape.reasons.append("英文-")
    return shape


def analyze(frags: list[Fragment], explicit: bool = False) -> Shape:
    """给候选片段打分（0~1）并记录加减分理由。

    分数只用来**分流**（明显像 / 明显不像 / 拿不准），不是最终判定：
    拿不准的交给模型。``explicit``（@机器人 / 引用消息）视为有意整活，放宽。
    """
    text = "\n".join(f.text for f in frags if f.text)
    norm = "".join(f.norm for f in frags)
    lines = [f.norm for f in frags if f.norm]
    # 整句是英文时走英文版式判断（中文字表对英文完全不起作用）
    if is_english(text):
        return analyze_english(text, explicit=explicit).with_lines(lines)
    shape = Shape(text=text, lines=lines)
    score = 0.5
    reasons = shape.reasons

    n = len(norm)
    if n < 4:
        score -= 0.45
        reasons.append("太短-")
    elif n <= 20:
        score += 0.08
        reasons.append("句长像歌词+")
    elif n <= 50:
        score += 0.02
    else:
        score -= 0.55
        reasons.append("太长-")

    if len(lines) >= 2:
        score += 0.12
        reasons.append("分行+")
        rhymes = [rhyme_key(line) for line in lines if line]
        repeats = sum(1 for a, b in zip(rhymes, rhymes[1:]) if a and a == b)
        if repeats:
            score += min(0.15, 0.08 * repeats)
            reasons.append("押韵+")

    tail = text.strip()[-1:] if text.strip() else ""
    if tail in _PUNCT_END:
        score -= 0.1
        reasons.append("句末标点-")
    if any(p in text for p in ("→", "=>", "http://", "https://")):
        score -= 0.3
        reasons.append("链接/箭头-")

    song_hits = sum(1 for ch in norm if ch in _SONG_WORDS or ch in _LYRIC_FILLERS)
    density = song_hits / max(1, n)
    if density >= 0.25:
        score += 0.1
        reasons.append("歌词语感+")
    elif density <= 0.17:
        score -= 0.14
        reasons.append("歌词语感-")

    raw_low = text.lower()
    chat_hits = [w for w in _CHAT_WORDS if w in raw_low]
    if chat_hits:
        score -= min(0.4, 0.14 * len(chat_hits))
        reasons.append("口语词-")
    if any(k in raw_low for k in _TITLE_INTENT):
        score -= 0.24
        reasons.append("点歌意图-")

    digits = sum(1 for ch in text if ch.isdigit())
    if digits >= 3:
        score -= 0.15
        reasons.append("数字多-")

    # 疑问句 / 征询口吻。注意：歌词里也常用「吗/吧」收尾（「当初的愿望实现了吗」），
    # 所以只在**很短**的句子才重罚，长句子轻轻减一点。
    if tail in _QUESTION_TAIL or "？" in text or "?" in text:
        score -= 0.13 if n <= 12 else 0.06
        reasons.append("疑问句-")

    if explicit:
        score += 0.2
        reasons.append("显式调用+")

    shape.score = max(0.0, min(1.0, score))
    return shape


def route(shape: Shape, skip_threshold: float = 0.3, ask_threshold: float = 0.5) -> str:
    """分流：``"skip"``（明显不像，别调模型）/ ``"obvious"``（很可能，但仍调模型确认）/ ``"ask"``。

    注意：本地判断**从不直接接龙**——「像歌词」不等于「是这句歌词」，
    最终仍由模型确认，只是对于明显不像的消息省掉这次调用。
    """
    if shape.score <= skip_threshold:
        return "skip"
    if shape.score >= ask_threshold:
        return "obvious"
    return "ask"

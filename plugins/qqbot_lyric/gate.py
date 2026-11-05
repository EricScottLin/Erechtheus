"""本地版式闸门：先用「歌词长什么样」的规律筛一遍，再决定要不要问模型。

歌词不是随便一句话。经验上歌词片段往往是：

- **很短**（一句 4~30 字），不以句号收尾；
- **分行**（多人发歌词常常一句一行、连发几条）；
- **没有话题化的口头语**（「咱们」「你们」+ 时间地点这类开会/吃饭的句子）；
- **有韵脚**（把整行近似拼音后，相邻行的结尾音相同/相近）；
- **有歌词语感词**（副歌高频的「我/你/想/爱/心/泪/夜……」密度高）。

这里只做**减分**，不做最终判定：分数太低的直接丢（省一次模型调用），
分数中上的交给模型确认。所有阈值都可以在 :mod:`config` 里调。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


from dataclasses import dataclass, field

from .rhyme import rhyme_key
from .text import Fragment

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


@dataclass
class Shape:
    """一段候选歌词的版式特征。"""

    text: str
    score: float = 0.5
    reasons: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def analyze(frags: list[Fragment], explicit: bool = False) -> Shape:
    """给候选片段打分（0~1）并记录加减分理由。

    分数只用来**分流**（明显像 / 明显不像 / 拿不准），不是最终判定：
    拿不准的交给模型。``explicit``（@机器人 / 引用消息）视为有意整活，放宽。
    """
    text = "\n".join(f.text for f in frags if f.text)
    norm = "".join(f.norm for f in frags)
    lines = [f.norm for f in frags if f.norm]
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

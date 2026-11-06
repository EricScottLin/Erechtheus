"""歌词匹配：把「群友发的那段字」定位到某首歌的某一行，并返回**下一句**。

匹配策略（从便宜到贵）：

1. **整行精确命中**（本地行索引）—— 见过这首歌后，几乎零成本；
2. **包含/近似**：在已知歌（内存里有的、近期接过的）的歌词里做滑动窗口打分；
3. **候选歌名扫描**：模型给的歌名线索 → lrclib 取歌词 → 再按 1/2 比对；
4. **网易云兜底**：以上都不中，用关键词去网易云搜歌名，能拿到歌名就回 lrclib 取歌词。

「整段歌词里若干行对不上」时允许只命中其中连续的一两行 —— 群友的片段往往
带口头禅、错别字，追求逐字全等是接不上的。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import difflib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .library import LyricLine, Library, Song, clean_title
from .text import Fragment, normalize, normalize_variants

# 只保留中文/英数用于比对（标点、emoji 已在 normalize 去掉）
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


@dataclass
class Hit:
    """一次匹配结果。"""

    song: Song
    score: float
    lines: list[str]  # 这首歌的全部歌词行（原始展示文本，按行号）
    start: int  # 命中的第一行行号
    end: int  # 命中的最后一行行号
    how: str = ""  # 命中方式（exact / window / candidate / netease / content）

    @property
    def next_lines(self) -> list[str]:
        """命中片段之后的若干行（最多 3 行，够展示）。"""
        if not self.lines:
            return []
        out = []
        for idx in range(self.end + 1, min(len(self.lines), self.end + 4)):
            line = self.lines[idx].strip()
            if line:
                out.append(line)
        return out

    @property
    def next_line(self) -> str:
        lines = self.next_lines
        return lines[0] if lines else ""

    @property
    def matched_text(self) -> list[str]:
        return [self.lines[i] for i in range(self.start, min(self.end + 1, len(self.lines)))]


def similarity(a: str, b: str) -> float:
    """归一化后的相似度（0~1）。完全包含取 1.0，否则用 SequenceMatcher。"""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short in long:
        # 包含关系：长度比例越接近越可信（片段常常只记得半句）
        ratio = len(short) / len(long)
        return 0.6 + 0.35 * ratio
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def best_window(text_norm: str, lines: list[str], max_window: int = 3) -> tuple[float, int, int]:
    """在一首歌的歌词里滑窗，找与 ``text_norm`` 最像的连续行。

    返回 ``(score, start, end)``。窗口从 1 行到 ``max_window`` 行。

    评分要点（这两条是「接不上」的主要历史原因，改动前务必跑单测）：

    - **部分命中**：群友常把一句歌词按逗号拆成几行发，或者只记得半句。
      所以除了「整段窗口 vs 整段输入」，还算「窗口里连续几行 vs 输入里连续几段」，
      取最高分。例：``daring I will, be loving you till we're 70`` 对上
      ``And darlin' I will`` / ``Be lovin' you`` / ``Till we're seventy`` 三行。
    - **行序校验**：输入里各段在歌词里的行号必须**严格递增**（群友按顺序唱），
      否则「拼了别人的话」也能凑出高分。
    """
    # 比对用「去空格」的紧凑形态；返回的行号仍然指向**原始歌词行**，
    # 所以接出来的下一句是带空格的正常英文。
    norm_lines = [normalize(line) for line in lines]
    best = (0.0, 0, 0)
    total = len(norm_lines)
    if not text_norm or total == 0:
        return best
    for size in range(1, max_window + 1):
        for start in range(0, total - size + 1):
            chunk = "".join(norm_lines[start : start + size])
            if not chunk:
                continue
            # 长度差太多直接跳过（省 SequenceMatcher 的时间）。
            # 歌词行本身较短，片段可能只记得半句，所以放宽到 60%。
            if abs(len(chunk) - len(text_norm)) > max(6, int(len(max(chunk, text_norm, key=len)) * 0.6)):
                continue
            score = similarity(text_norm, chunk)
            if score > best[0]:
                best = (score, start, start + size - 1)
    partial = _partial_window(text_norm, norm_lines, max_window)
    if partial is not None and partial[0] > best[0]:
        best = partial
    # 多段输入（群友分几行发）时，整段窗口相似度会被「分行位置不同」拉低
    # （三行写成一句、或者把上一句也带上了）。这时**行序对得上**才是关键证据：
    # 按「输入有多少字按顺序出现在窗口里」折算一次，避免该接的接不上。
    if "\n" in text_norm and best[0] > 0:
        chunk = "".join(norm_lines[best[1] : best[2] + 1])
        if chunk:
            covered = _coverage(chunk, text_norm)
            boosted = 0.6 * best[0] + 0.4 * covered
            if boosted > best[0]:
                best = (min(1.0, boosted), best[1], best[2])
    return best


def _coverage(chunk: str, text: str) -> float:
    """输入里有多大比例的字，按顺序出现在歌词片段里（LCS / 输入长度）。"""
    if not text:
        return 0.0
    n, m = len(text), len(chunk)
    if n * m > 400_000:  # 太长就不算（歌词行不会这么长）
        return 0.0
    prev = [0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        ch = text[i - 1]
        for j in range(1, m + 1):
            cur[j] = prev[j - 1] + 1 if ch == chunk[j - 1] else max(prev[j], cur[j - 1])
        prev = cur
    return prev[m] / n


def _segment_offsets(parts: list[str]) -> list[int]:
    """各段在拼接文本里的起始下标。"""
    out: list[int] = []
    pos = 0
    for part in parts:
        out.append(pos)
        pos += len(part)
    return out


def _partial_window(
    text_norm: str, norm_lines: list[str], max_window: int
) -> Optional[tuple[float, int, int]]:
    """「部分命中」：输入里连续几段 ≈ 歌词里连续几行（允许漏掉头尾）。

    逐行滑窗（行号窗口 ``[start, end]``，最多 ``max_window`` 行），
    把输入切成「落在该行里」的若干段（段边界 = 输入里的分行位置），
    行号严格递增的连续行才算一次命中；得分 = 覆盖到的输入字数 / 输入总字数
    × 相似度均值 × 跳行折扣。返回 ``(score, start, end)``，没有命中返回 ``None``。

    注意这里的 ``end`` 是**最后一段对上的那一行**，不是窗口末尾：
    接出来的「下一句」必须是群友刚唱完那句之后的那一行，否则会跳过好几行。
    """
    if not text_norm:
        return None
    parts = text_norm.split("\n")
    if len(parts) < 2:
        return None
    offsets = _segment_offsets(parts)
    best: Optional[tuple[float, int, int]] = None
    total = len(norm_lines)
    for start in range(total):
        end = min(total - 1, start + max_window - 1)
        line_of: list[int] = []
        ok = True
        for idx, part in enumerate(parts):
            lo, hi = offsets[idx], offsets[idx] + len(part)
            if not part:
                line_of.append(-1)
                continue
            found = -1
            for line_idx in range(start, end + 1):
                line = norm_lines[line_idx]
                if not line:
                    continue
                if _overlaps(line, text_norm, lo, hi) and (not line_of or line_idx > line_of[-1]):
                    found = line_idx
                    break
            line_of.append(found)
            if found < 0:
                ok = False
                break
        if not ok:
            continue
        # 合并「同一行里的相邻段」，逐行算相似度
        groups: list[tuple[int, str]] = []
        for idx, line_idx in enumerate(line_of):
            if line_idx < 0:
                continue
            if groups and groups[-1][0] == line_idx:
                groups[-1] = (line_idx, groups[-1][1] + parts[idx])
            else:
                groups.append((line_idx, parts[idx]))
        if len(groups) < 2:
            continue
        covered = sum(len(text) for _, text in groups)
        if covered < 0.6 * len(text_norm):
            continue
        sims = [similarity(text, norm_lines[line_idx]) for line_idx, text in groups]
        avg = sum(sims) / len(sims)
        first, last = groups[0][0], groups[-1][0]
        score = (covered / len(text_norm)) * avg * _span_penalty(last - first + 1, len(groups))
        if best is None or score > best[0]:
            best = (score, first, last)
    return best


def _span_penalty(span: int, used: int) -> float:
    """命中区间里有没对上的行时打折（跳得越多越不可信）。"""
    if span <= used:
        return 1.0
    return max(0.6, used / span)


def _overlaps(line: str, text: str, lo: int, hi: int, min_len: int = 3) -> bool:
    """输入里 ``text[lo:hi]`` 这一段，是否和歌词行 ``line`` 有足够长的公共片段。

    群友的分行位置和歌词库的分行位置经常对不上（「daring I will, be loving you
    till we're 70」在一行里，歌词库里是三行），所以不能要求「整段落在某一行内」，
    只要这一行里存在一段连续重叠（>= ``min_len`` 个字）就算这一段归这一行。
    """
    span = text[lo:hi]
    if not span:
        return False
    for size in range(min(len(span), len(line)), min_len - 1, -1):
        for i in range(0, len(span) - size + 1):
            if span[i : i + size] in line:
                return True
    return False


@dataclass
class SearchContext:
    """一次匹配用的上下文。"""

    text_norm: str
    texts: list[str] = field(default_factory=list)  # 原始候选文本（可多行）
    hints: list[str] = field(default_factory=list)  # 模型给的歌名/歌手线索
    hint_title: str = ""
    hint_artist: str = ""
    explicit_title: str = ""  # 用户明确点名的歌（/歌词 xxx）
    recent_song: Optional[Song] = None  # 最近正在接的歌
    allow_network: bool = True

    @classmethod
    def build(
        cls,
        frags: list[Fragment],
        hint_title: str = "",
        hint_artist: str = "",
        explicit_title: str = "",
        recent_song: Optional[Song] = None,
        allow_network: bool = True,
    ) -> "SearchContext":
        texts = [f.text for f in frags if f.text]
        hints = [x for x in (hint_title, hint_artist, f"{hint_title} {hint_artist}".strip()) if x]
        return cls(
            text_norm="".join(f.norm for f in frags),
            texts=texts,
            hints=hints,
            hint_title=hint_title,
            hint_artist=hint_artist,
            explicit_title=explicit_title,
            recent_song=recent_song,
            allow_network=allow_network,
        )


def hint_bonus(song: Song, ctx: SearchContext, bonus: float) -> float:
    """模型给的歌名/歌手与这首歌的吻合度加分。"""
    if bonus <= 0 or not (ctx.hint_title or ctx.hint_artist):
        return 0.0
    title_sim = similarity(ctx.hint_title, clean_title(song.title)) if ctx.hint_title else 0.0
    artist_sim = similarity(ctx.hint_artist, song.artist) if ctx.hint_artist else 0.0
    if title_sim >= 0.6:
        return bonus * min(1.0, 0.6 + 0.4 * title_sim) + (0.03 if artist_sim >= 0.5 else 0.0)
    return 0.0


async def find_in_songs(
    library: Library,
    songs: Iterable[Song],
    ctx: SearchContext,
    min_score: float,
    window: int = 3,
) -> Optional[Hit]:
    """在给定的几首歌里找最像的那一处（按需拉歌词、进索引）。"""
    best: Optional[Hit] = None
    for song in songs:
        lines = await library.ensure_lyrics(song)
        if not lines:
            continue
        score, start, end = best_window(ctx.text_norm, lines, window)
        score += hint_bonus(song, ctx, 0.12)
        if score > (best.score if best else 0.0):
            best = Hit(song=song, score=min(1.0, score), lines=lines, start=start, end=end, how="window")
    return best


def exact_hits(library: Library, text: str, recent: Optional[Song] = None) -> list[Hit]:
    """整行精确命中（本地索引，零网络）。"""
    entries = library.index.lookup(text)
    if not entries:
        return []
    out: list[Hit] = []
    for song, idx in entries:
        lines = library.index.lines_of(song)
        if not lines or idx >= len(lines):
            continue
        out.append(Hit(song=song, score=1.0, lines=lines, start=idx, end=idx, how="exact"))
    # 正在接的那首歌优先
    if recent is not None:
        out.sort(key=lambda h: (h.song.key != recent.key, -h.score))
    return out


async def search(
    library: Library,
    ctx: SearchContext,
    min_score: float = 0.82,
    allow_netease: bool = True,
) -> Optional[Hit]:
    """主入口：返回得分超过 ``min_score`` 的最佳命中；没有则返回 ``None``。

    顺序按「命中概率 × 成本」排：先本地，再便宜的精确查询，最后才做可能很慢的
    兜底（显著词 / 网易云）。任何一步真的找到了就立刻返回，不做无谓的重试。
    """
    # 1) 精确整行命中（本地索引，零网络）
    if ctx.texts:
        for hit in exact_hits(library, ctx.texts[-1], ctx.recent_song):
            if ctx.recent_song is not None and hit.song.key == ctx.recent_song.key:
                hit.score = 1.0
            if hit.score >= min_score and hit.next_line:
                return hit

    # 2) 已知歌里扫描（含最近正在接的那首）
    known = library.index.songs()
    if ctx.recent_song is not None and all(s.key != ctx.recent_song.key for s in known):
        known.append(ctx.recent_song)
    if known:
        hit = await find_in_songs(library, known, ctx, min_score)
        if hit is not None and hit.score >= min_score:
            return hit

    if not ctx.allow_network:
        return None

    # 3) 模型给的歌名线索（最可靠）：按歌名精确取歌词再比对。
    #    换成「歌词里滑窗找这句」而不是「歌名跟这句有多像」——
    #    模型猜歌名靠的是歌词内容，拿歌名跟歌词比相似度几乎是零分。
    if ctx.hint_title:
        pairs = await library.fetch_by_title(ctx.hint_title)
        best: Optional[Hit] = None
        for song, lines in pairs:
            score, start, end = best_window(ctx.text_norm, lines, 3)
            score += hint_bonus(song, ctx, 0.12)
            if best is None or score > best.score:
                best = Hit(song=song, score=min(1.0, score), lines=lines, start=start, end=end, how="hint")
        if best is not None and best.score >= min_score:
            return best

    # 4) 用户显式点名的歌（/歌词 的标题、引用消息里的《晴天》）
    if ctx.explicit_title:
        songs = await library.search_songs(ctx.explicit_title, limit=6)
        hit = await find_in_songs(library, songs, ctx, min_score)
        if hit is not None and hit.score >= min_score:
            hit.how = "title"
            return hit

    # 5) 片段里的显著词直接查（歌名常藏在歌词里，如「小幸运」）；只试 2 个
    for phrase in salience_queries(ctx.texts, max_queries=1):
        songs = await library.search_songs(phrase, limit=5)
        if not songs:
            continue
        hit = await find_in_songs(library, songs, ctx, min_score)
        if hit is not None and hit.score >= min_score:
            return hit

    # 6) 网易云兜底（最慢，放最后）。网易云搜索是「按内容」的：
    #    直接拿整句歌词去搜，就能搜到它所属的歌（「我听见雨滴落在青青草地」→ 小幸运）。
    #    lrclib 的模糊搜索做不到这点（实测同一句返回 0 条）。
    #    命中后直接取网易云的歌词 —— 连 lrclib 没收录的歌也能接上。
    if allow_netease and library.netease is not None:
        for phrase in netease_queries(ctx):
            found = await library.netease_lyric_lookup(phrase, ctx.text_norm)
            if not found:
                continue
            song, lines = found
            score, start, end = best_window(ctx.text_norm, lines, 3)
            if score >= min_score:
                return Hit(song=song, score=min(1.0, score), lines=lines,
                           start=start, end=end, how="netease")
    return None


def netease_queries(ctx: SearchContext) -> list[str]:
    """网易云搜索关键词：模型给的歌名线索 + 整句歌词 + 显著词。

    整句歌词放前面——网易云按内容搜很准；歌名线索在模型猜对时最快。
    """
    out: list[str] = []
    for item in hint_queries(ctx):
        out.append(item)
    for text in reversed(ctx.texts):
        text = text.strip()
        if 4 <= len(text) <= 40:
            out.append(text)
    out.extend(salience_queries(ctx.texts, max_queries=1)[:2])
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:3]


def _enough(candidates: list[Hit], min_score: float) -> bool:
    return any(h.score >= min_score for h in candidates)


def _pick(candidates: list[Hit], min_score: float) -> Optional[Hit]:
    usable = [h for h in candidates if h.score >= min_score and h.next_line]
    if not usable:
        return None
    usable.sort(key=lambda h: (-h.score, -len(h.next_line)))
    return usable[0]


def hint_queries(ctx: "SearchContext") -> list[str]:
    """模型给的歌名线索（歌名、歌名+歌手）作为最后的查询关键词。"""
    out = []
    if ctx.hint_title:
        out.append(f"{ctx.hint_title} {ctx.hint_artist}".strip() if ctx.hint_artist else ctx.hint_title)
    return out


def salience_queries(texts: list[str], max_queries: int = 2) -> list[str]:
    """从片段里挑出像歌名的短语（歌名常是片段里最「点题」的那几个字）。

    做法很土但有效：取每段连续中文的 2~6 字子串里「在语料里罕见」的
    ——没有语料，于是退化为：取每段的开头 2~6 字 + 结尾 2~6 字各若干。
    真正的歌名线索主要来自模型；这里只是兜底。
    """
    out: list[str] = []
    for text in texts[-2:]:
        for run in _CJK_RUN.findall(text):
            if len(run) < 2:
                continue
            for size in (6, 5, 4, 3, 2):
                if len(run) >= size:
                    out.append(run[:size])
                    out.append(run[-size:])
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:max_queries * 6]

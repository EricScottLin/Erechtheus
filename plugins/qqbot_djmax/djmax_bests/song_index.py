"""曲库检索：按 ID / 曲名 / 歌手查找单曲。

V-Archive 的曲库是整表 JSON，这里在内存里建一份轻量索引：
- ``by_id``：songid → 条目
- ``all``：按曲名排序的条目列表
- 曲名与歌手统一小写、去符号后参与匹配，避免手打大小写与标点差异。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import DMSongDB, DMSongDBEntry

#: 曲名归一化时忽略的字符（空白、常见标点与装饰符号）。
_STRIP = re.compile(r"[\s\-_~!@#$%^&*()+=|\\/<>?.,:;'\"\[\]{}`　·・.]+")
#: 曲名末尾的版本后缀，例如 "V" / "Feat. XXX"。
_TRAILING = re.compile(r"\s*[\(（\[【].*?[\)）\]】]\s*$")


def normalize(text: str) -> str:
    """统一大小写、全半角并去标点，用于宽松匹配。"""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return _STRIP.sub("", text).strip()


@dataclass(frozen=True)
class SongHit:
    entry: DMSongDBEntry
    score: int
    """匹配得分，越大越可信。"""


class SongIndex:
    """曲库索引；``search`` 返回按相关度降序的候选。"""

    def __init__(self, db: DMSongDB) -> None:
        self.db = db
        self.by_id: dict[int, DMSongDBEntry] = {}
        self.all: list[DMSongDBEntry] = list(db.root)
        self._norm_title: dict[int, str] = {}
        self._norm_composer: dict[int, str] = {}
        self.rebuild()

    def rebuild(self) -> None:
        self.by_id = {e.songid: e for e in self.all}
        self._norm_title = {e.songid: normalize(e.title) for e in self.all}
        self._norm_composer = {e.songid: normalize(e.composer) for e in self.all}

    def get(self, songid: int) -> DMSongDBEntry | None:
        return self.by_id.get(int(songid))

    #: 匹配得分：越大越可信，用于在多种命中方式之间取舍。
    SCORE_ID = 100
    SCORE_TITLE_EXACT = 95
    SCORE_TITLE_STRIPPED = 85
    SCORE_TITLE_PREFIX = 80
    SCORE_TITLE_CONTAINS = 60
    SCORE_COMPOSER_EXACT = 50
    SCORE_COMPOSER_CONTAINS = 40

    def search(self, query: str, *, limit: int = 10) -> list[SongHit]:
        """按 songid → 全等曲名 → 前缀 → 子串 → 歌手的顺序收集候选。"""
        query = (query or "").strip()
        if not query:
            return []

        hits: dict[int, int] = {}
        # 0. 纯数字：既可能是 songid，也可能是纯数字曲名，两种都收集；
        #    "441" / "#441" 都算 songid，前缀 # 只是用户抄候选列表时的写法。
        if (sid_text := query.lstrip("#＃")) and sid_text.isdigit():
            if (sid := int(sid_text)) in self.by_id:
                hits[sid] = self.SCORE_ID

        needle = normalize(query)
        if not needle:
            return self._hits(hits, limit)

        for entry in self.all:
            sid = entry.songid
            title = self._norm_title.get(sid, "")
            score = 0
            if title == needle:
                score = self.SCORE_TITLE_EXACT
            elif title.startswith(needle):
                score = self.SCORE_TITLE_PREFIX
            elif needle in title:
                score = self.SCORE_TITLE_CONTAINS
            else:
                # 曲名去掉括号后缀再试一次，方便搜 "Your Priestess" 这类带副标题的曲目。
                stripped = _TRAILING.sub("", entry.title).strip()
                if stripped and (norm_stripped := normalize(stripped)) and norm_stripped == needle:
                    score = max(score, self.SCORE_TITLE_STRIPPED)
                elif self._norm_composer.get(sid, "") == needle:
                    score = max(score, self.SCORE_COMPOSER_EXACT)
                elif needle and needle in self._norm_composer.get(sid, ""):
                    score = max(score, self.SCORE_COMPOSER_CONTAINS)
            if score:
                # 同分时短曲名更可能是目标曲目。
                hits[sid] = max(hits.get(sid, 0), score - min(len(title) // 20, 5))
        return self._hits(hits, limit)

    def _hits(self, scores: dict[int, int], limit: int) -> list[SongHit]:
        entries = [(sid, s) for sid, s in scores.items() if sid in self.by_id]
        entries.sort(key=lambda item: (-item[1], len(self.by_id[item[0]].title), item[0]))
        return [SongHit(self.by_id[sid], score) for sid, score in entries[:limit]]


#: 最高分领先次高分多少分，才算"没有歧义"。
AMBIGUOUS_GAP = 15


@dataclass(frozen=True)
class ResolveResult:
    """一次查询的判定结果。

    ``entry`` 非空表示可以确定曲目；否则 ``hits`` 是待用户确认的候选，
    ``reason`` 说明为什么不能确定（``name`` / ``composer`` / ``ids``）。
    """

    entry: DMSongDBEntry | None = None
    hits: tuple[SongHit, ...] = ()
    reason: str = ""


def resolve(index: SongIndex, query: str, *, hint: str | None = None) -> ResolveResult:
    """把用户输入解析成唯一曲目；确实有歧义时才返回候选。

    判定顺序（先严格后宽松，越靠前越可信）：

    1. 纯数字且是合法 songid → 直接命中；禁止再用它去匹配曲名/歌手
       （否则 ``#441`` 会连带出 "AD2222" 这类含 441 的曲名）。
    2. 曲名全等且曲名方只有这一个 → 命中。
    3. 同名不同曲（例如两首 "Alone"）：用 ``hint``（用户顺带写的 songid）
       消歧，否则让用户确认。
    4. 歌手全等只有一个 → 命中（与曲名全等的那个同名时按曲名走）。
       歌手全等有多个（例如 Nauts 有几十首）：有 hint 用 hint，
       否则列出这位歌手的歌曲让用户挑，绝不拿其中一首随便应付。
       歌手子串命中只有一个时同样可用（例如 "Marshm"）。
    5. 其余情况（前缀 / 子串命中，往往一个字母就能匹配到十几首）：
       最高分领先次高分 ``AMBIGUOUS_GAP`` 分以上才直接命中。
    """
    hits = index.search(query)
    if not hits:
        return ResolveResult(reason="none")

    sid_text = query.strip().lstrip("#＃")
    if sid_text.isdigit():
        entry = index.get(int(sid_text))
        if entry is not None:
            return ResolveResult(entry=entry, hits=(SongHit(entry, SongIndex.SCORE_ID),))
        return ResolveResult(hits=tuple(hits), reason="ids")

    needle = normalize(query)
    entries = [h.entry for h in hits]
    hinted = _from_hint(index, hint)

    # 2/3. 曲名全等（含去掉括号后缀后的全等）。
    exact_title = [e for e in entries if _is_exact_title(e, needle)]
    if len(exact_title) == 1:
        return ResolveResult(entry=exact_title[0], hits=tuple(hits))
    if len(exact_title) > 1:
        if hinted is not None and hinted.songid in {e.songid for e in exact_title}:
            return ResolveResult(entry=hinted, hits=tuple(_rank(hits, [hinted])))
        return ResolveResult(hits=tuple(_rank(hits, exact_title)), reason="name")

    # 4. 歌手全等。
    exact_composer = [e for e in entries
                      if normalize(e.composer) and normalize(e.composer) == needle]
    if len(exact_composer) == 1:
        return ResolveResult(entry=exact_composer[0], hits=tuple(hits))
    if len(exact_composer) > 1:
        if hinted is not None and hinted.songid in {e.songid for e in exact_composer}:
            return ResolveResult(entry=hinted, hits=tuple(_rank(hits, [hinted])))
        return ResolveResult(hits=tuple(_rank(hits, exact_composer)), reason="composer")
    in_composer = [e for e in entries
                   if normalize(e.composer) and needle in normalize(e.composer)]
    if len(in_composer) == 1:
        return ResolveResult(entry=in_composer[0], hits=tuple(hits))
    if in_composer:
        return ResolveResult(hits=tuple(_rank(hits, in_composer)), reason="composer")

    # 5. 只看曲名前缀 / 子串命中：领先足够多才认。
    if len(hits) == 1 or hits[0].score - hits[1].score >= AMBIGUOUS_GAP:
        return ResolveResult(entry=hits[0].entry, hits=(hits[0],))
    return ResolveResult(hits=tuple(hits), reason="name")


def _is_exact_title(entry: DMSongDBEntry, needle: str) -> bool:
    title = normalize(entry.title)
    if title == needle:
        return True
    stripped = _TRAILING.sub("", entry.title).strip()
    return bool(stripped) and normalize(stripped) == needle


def _from_hint(index: SongIndex, hint: str | None) -> DMSongDBEntry | None:
    if hint and hint.isdigit():
        return index.get(int(hint))
    return None


def _rank(hits: list[SongHit], wanted: list[DMSongDBEntry]) -> list[SongHit]:
    """把 ``wanted`` 里的条目排到候选前面，其余保持原顺序。"""
    order = {e.songid: i for i, e in enumerate(wanted)}
    return sorted(hits, key=lambda h: (order.get(h.entry.songid, len(order)), -h.score))

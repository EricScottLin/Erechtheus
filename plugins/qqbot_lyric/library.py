"""曲库客户端：lrclib（歌词库）+ 网易云搜索（补歌名）+ 本地行索引（O(1) 反查）。

设计要点（实测得出的结论，见 README「为什么这么设计」）：

1. **lrclib 只适合「歌名精确」的查询**。它的 ``q=`` 是模糊匹配，查「少年中国说」
   返回 0 条、「小幸运」的 20 条里没有一条副歌 —— 拿整句歌词去 lrclib 搜基本搜不到。
2. 所以**主路径是先猜歌**（模型给的歌名线索，或「小幸运」这种显著词），
   用歌名查 lrclib 拉全曲歌词，再在本地逐行比对。
3. 一旦见过某首歌，就把它的**每一行**写进本地行索引；下次群友发这句歌词时，
   直接命中、零网络请求。索引是「按需学习」的，用多了就越来越快。
4. **网易云搜索**只用来补「歌名+歌手」：lrclib 没收录的中文歌（例如张杰
   《少年中国说》）网易云能搜到，搜到后再回 lrclib 取歌词；取不到就只报歌名。

限流：lrclib 要求别太频繁，所有请求共用一个「最小间隔」闸门。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import asyncio
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable, Optional

import httpx
from nonebot.log import logger

from .text import normalize, normalize_variants, to_simplified

LRCLIB_UA = "qqbot-lyric/1.0 (QQ group lyric relay bot)"
NETEASE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


_GENERIC_PAREN_RE = re.compile(
    r"[（(\[【][^）)\]】]*(?:版|live|伴奏|remix|翻唱|cover|demo|现场|混音|片段|dj|DJ|女声|男声|"
    r"原唱|翻自|改编|remake)[^）)\]】]*[）)\]】]",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    """去掉「(Live)」「【伴奏版】」这类后缀，提高检索命中率。"""
    text = _GENERIC_PAREN_RE.sub("", title or "")
    text = re.sub(r"\s*-\s*(?:live|remix|伴奏|纯音乐).*$", "", text, flags=re.I)
    return " ".join(text.split()).strip()


# LRC 里的制作信息行（不是歌词，不能当「下一句」接出去）
_CREDIT_RE = re.compile(
    r"^\s*(作词|作曲|编曲|混音|后期|制作人|录音|母带|监制|出品|策划|"
    r"lyrics?\s*(by|:)|compos(er|ed)|arrang(er|ed)|produced|mixed|mastered|"
    r"written by|music by|words by|vocal|歌手|专辑|词|曲)\s*[:：/]",
    re.IGNORECASE,
)


def is_credit_line(line: str) -> bool:
    """这行是不是「作词/作曲/Lyrics by」这类制作信息（不能当歌词接）。"""
    text = (line or "").strip()
    if not text:
        return True
    return bool(_CREDIT_RE.match(text))


@dataclass
class Song:
    """一首歌的元信息（可能还没有歌词）。"""

    id: str  # 来源内唯一：lrclib 的 id / netease 的 song id
    title: str
    artist: str
    album: str = ""
    duration: float = 0.0
    source: str = "lrclib"
    has_lyrics: bool = False
    # 歌词本身就是从曲库来的（网易云/本地）——这类命中才算「曲库确认过」，
    # 用模型猜的歌名再去取歌，只能算「模型说它像某首歌」。
    from_library: bool = False

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def label(self) -> str:
        return f"{self.title} - {self.artist}" if self.artist else self.title

    def to_json(self) -> dict:
        return {
            "id": self.id, "title": self.title, "artist": self.artist,
            "album": self.album, "duration": self.duration,
            "source": self.source, "has_lyrics": self.has_lyrics,
            "from_library": self.from_library,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Song":
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            album=str(data.get("album", "")),
            duration=float(data.get("duration") or 0.0),
            source=str(data.get("source", "lrclib")),
            has_lyrics=bool(data.get("has_lyrics")),
            from_library=bool(data.get("from_library")),
        )


def parse_lrc(text: str) -> list[str]:
    """LRC → 纯歌词行（去时间戳、去空行、去制作信息、繁体转简体）。"""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = re.sub(r"^\[[^\]]*\]", "", raw).strip()
        if not line or is_credit_line(line):
            continue
        lines.append(to_simplified(line))
    return lines


# 双语权重：压缩形态（去空格）命中一般更可信；原形态命中用来兜住空格差异
_WEIGHT_CONSENSUS = 0.1


def _window_score(text_norm: str, lines: list[str], raw: str = "", compact: str = "") -> float:
    """一句歌词在某个版本歌词里的得分（多个形态取最高 + 一致性加成）。

    只用压缩形态会把「朋友一生一起走 那些日子不再有」这类基本对上的句子
    打分偏低（空格位置不同）；只用原形态又容易被「同名词组」骗。
    所以两边都算，取最高，两边都不错时再给一点加成。
    """
    from .search import best_window

    scores = [best_window(text_norm, lines, 3)[0]]
    if raw:
        scores.append(best_window(raw, lines, 3)[0])
    if compact:
        scores.append(best_window(compact, lines, 3)[0])
    best = max(scores)
    if len(scores) >= 2 and sorted(scores)[-2] >= 0.6:
        best += _WEIGHT_CONSENSUS
    return min(1.0, best)


def parse_plain(text: str) -> list[str]:
    """纯文本歌词 → 行列表（去制作信息、繁体转简体）。

    lrclib 上相当多的中文歌词是繁体（《晴天》《小幸运》都是），
    不转简体的话大陆群友打的简体歌词永远匹配不上。
    """
    return [
        " ".join(to_simplified(line).split())
        for line in (text or "").splitlines()
        if line.strip() and not is_credit_line(line)
    ]


@dataclass
class LyricLine:
    """索引里的一行歌词。"""

    norm: str  # 归一化后（去标点空白）
    text: str  # 原始展示文本
    index: int  # 在该歌曲歌词里的行号（0 起）
    ts: float = field(default_factory=time.time)


class RateLimiter:
    """异步最小间隔闸门（lrclib 要求克制的请求频率）。"""

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = max(0.0, interval)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def __aenter__(self) -> "RateLimiter":
        await self._lock.acquire()
        now = time.monotonic()
        wait = self.interval - (now - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()
        return self

    async def __aexit__(self, *exc) -> None:
        self._lock.release()


class HttpGetter:
    """统一 GET：超时、UA、限流、异常吞掉（返回 ``None``）。"""

    def __init__(self, timeout: float, limiter: Optional[RateLimiter] = None) -> None:
        self.timeout = timeout
        self.limiter = limiter or RateLimiter(0.25)
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(5.0, self.timeout)),
                headers={"User-Agent": LRCLIB_UA, "Accept": "application/json"},
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def get_json(self, url: str, params: Optional[dict] = None) -> object | None:
        try:
            async with self.limiter:
                resp = await self._get_client().get(url, params=params)
            if resp.status_code != 200:
                logger.debug(f"[qqbot_lyric] HTTP {resp.status_code} {url}")
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.debug(f"[qqbot_lyric] 请求失败 {url}：{type(e).__name__} {e}")
            return None


class Lrclib:
    """lrclib.net 客户端（免费、无需 key）。"""

    def __init__(self, base: str, timeout: float, max_lines: int = 400) -> None:
        self.base = (base or "https://lrclib.net").rstrip("/")
        self.http = HttpGetter(timeout, RateLimiter(0.3))
        self.max_lines = max_lines

    async def close(self) -> None:
        await self.http.close()

    async def search(self, q: str, limit: int = 3) -> list[Song]:
        """按关键词（歌名 / 歌名+歌手）搜索。"""
        q = (q or "").strip()
        if not q:
            return []
        data = await self.http.get_json(f"{self.base}/api/search", {"q": q})
        if not isinstance(data, list):
            return []
        songs: list[Song] = []
        for item in data[:limit]:
            try:
                songs.append(
                    Song(
                        id=str(item.get("id", "")),
                        title=str(item.get("trackName") or item.get("name") or ""),
                        artist=str(item.get("artistName") or ""),
                        album=str(item.get("albumName") or ""),
                        duration=float(item.get("duration") or 0),
                        source="lrclib",
                    )
                )
            except (TypeError, ValueError):
                continue
        # 同名 + 同歌手 / 歌手互为别名的多个版本只留一个：
        # 网易云常返回「RyaVocal」「Jay」这类偏门歌手版本，把名额省给原型歌手
        seen: set[str] = set()
        out: list[Song] = []
        for song in songs:
            key = clean_title(song.title)
            if key in seen:
                continue
            seen.add(key)
            out.append(song)
        return out

    async def lyrics(self, song: Song) -> Optional[list[str]]:
        """取整首歌词（优先同步歌词，其次纯文本）。"""
        data = await self.http.get_json(f"{self.base}/api/get/{song.id}")
        if isinstance(data, dict):
            lines = parse_lrc(data.get("syncedLyrics") or "") or parse_plain(
                data.get("plainLyrics") or ""
            )
            if lines:
                song.has_lyrics = True
                if not song.artist:
                    song.artist = str(data.get("artistName") or "")
                return lines[: self.max_lines]
        # /api/get 偶尔抽风时退回搜索结果的 lyrics 字段
        data = await self.http.get_json(f"{self.base}/api/search", {"q": song.title})
        if isinstance(data, list):
            for item in data:
                if str(item.get("id")) != song.id:
                    continue
                lines = parse_lrc(item.get("syncedLyrics") or "") or parse_plain(
                    item.get("plainLyrics") or ""
                )
                if lines:
                    song.has_lyrics = True
                    return lines[: self.max_lines]
        return None


class Netease:
    """网易云：按内容搜歌 + 直接取歌词（未登录接口，不保证稳定，失败就放弃）。

    这两个能力是社区插件的通行做法：酷我插件 [1] 用
    ``m.kuwo.cn/newh5/singles/songinfoandlrc``、通用点歌插件 [2] 同时支持
    网易/QQ/酷狗/咪咕、AstrBot 歌词接龙插件 [3] 也走「按内容搜歌」。
    我们只借它补 lrclib 的缺口，不依赖它的稳定性（失败自动退避 5 分钟）。
    """

    SEARCH_URL = "https://music.163.com/api/search/get"
    # 歌词接口：返回 {"lrc": {"lyric": "[00:00.00]..."},"tlyric": {...}} 或 {"nolyric": true}
    LYRIC_URL = "https://music.163.com/api/song/lyric"

    def __init__(self, timeout: float, limit: int = 3) -> None:
        self.timeout = timeout
        self.limit = limit
        self._client: Optional[httpx.AsyncClient] = None
        self._broken_until = 0.0  # 连续失败后退避，别每条消息都硬等

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(5.0, self.timeout)),
                headers={
                    "User-Agent": NETEASE_UA,
                    "Referer": "https://music.163.com/",
                    "Cookie": "NMTID=00O",
                },
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._broken_until

    async def search(self, keywords: str) -> list[Song]:
        if not self.available:
            return []
        try:
            resp = await self._get_client().get(
                self.SEARCH_URL,
                params={"s": keywords, "type": 1, "limit": self.limit, "offset": 0},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
        except Exception as e:  # noqa: BLE001 —— 网易云失败是常态，不应影响主流程
            logger.debug(f"[qqbot_lyric] 网易云搜索失败：{type(e).__name__} {e}")
            self._broken_until = time.monotonic() + 300
            return []
        result = (data or {}).get("result") or {}
        out: list[Song] = []
        for item in (result.get("songs") or [])[: self.limit]:
            artists = item.get("artists") or item.get("ar") or []
            song = Song(
                id=str(item.get("id", "")),
                title=str(item.get("name") or ""),
                artist="、".join(str(a.get("name", "")) for a in artists),
                album=str((item.get("album") or {}).get("name") or ""),
                duration=float(item.get("duration") or 0) / 1000.0,
                source="netease",
            )
            out.append(song)
        return out

    async def lyrics(self, song: Song) -> Optional[list[str]]:
        """网易云歌词（LRC → 行列表，顺便转简体）。取不到返回 None。"""
        if not song.id or not self.available:
            return None
        try:
            resp = await self._get_client().get(
                self.LYRIC_URL, params={"id": song.id, "lv": -1, "kv": -1, "tv": -1}
            )
            if resp.status_code != 200:
                logger.debug(f"[qqbot_lyric] 网易云歌词 HTTP {resp.status_code}")
                return None
            data = resp.json()
        except Exception as e:  # noqa: BLE001 —— 网易云不稳定，失败不影响主流程
            logger.debug(f"[qqbot_lyric] 网易云歌词失败：{type(e).__name__} {e}")
            self._broken_until = time.monotonic() + 300
            return None
        if not isinstance(data, dict) or data.get("nolyric"):
            return None
        raw = (data.get("lrc") or {}).get("lyric") or ""
        lines = parse_lrc(raw)
        return lines or None


class LineIndex:
    """本地歌词行索引：归一化行 → (歌, 行号)。

    只做「精确整行命中」——歌词里同一句一般只属于一首歌，精确命中几乎不会错，
    而且快（O(1)）。模糊「包含」命中由 :mod:`search` 的序列扫描负责。
    """

    def __init__(self, capacity: int = 200000) -> None:
        self.capacity = max(1000, int(capacity))
        self._index: OrderedDict[str, list[tuple[Song, int]]] = OrderedDict()
        self._songs: dict[str, list[str]] = {}  # song.key -> 归一化行列表（按行号）
        self._display: dict[str, list[str]] = {}  # song.key -> 原始展示行（保留空格）
        self._meta: dict[str, Song] = {}

    def __len__(self) -> int:
        return len(self._index)

    @property
    def song_count(self) -> int:
        return len(self._meta)

    def add_song(self, song: Song, lines: Iterable[str]) -> None:
        """把一首歌的每一行写进索引。

        每行同时索引「原文归一」和「简体归一」两种形态：lrclib 的歌词大多是
        繁体，群友打简体时两种形态必须都能命中（"愛你孤身走暗巷" ↔ "爱你孤身走暗巷"）。
        """
        display = [str(line) for line in lines]
        norm_lines = [normalize_variants(line) for line in display]
        key = song.key
        self._meta[key] = song
        self._songs[key] = norm_lines
        self._display[key] = display
        for idx, forms in enumerate(norm_lines):
            for norm in forms:
                if len(norm) < 3:
                    continue
                bucket = self._index.setdefault(norm, [])
                entry = (song, idx)
                if entry not in bucket:
                    bucket.append(entry)
                self._index.move_to_end(norm)
        while len(self._index) > self.capacity:
            self._index.popitem(last=False)

    def lookup(self, text: str) -> list[tuple[Song, int]]:
        out: list[tuple[Song, int]] = []
        seen: set[tuple[str, int]] = set()
        for norm in normalize_variants(text):
            hits = self._index.get(norm)
            if hits is None:
                continue
            self._index.move_to_end(norm)
            for song, idx in hits:
                if (song.key, idx) in seen:
                    continue
                seen.add((song.key, idx))
                out.append((song, idx))
        return out

    def lines_of(self, song: Song) -> list[str]:
        """展示用的歌词行。

        索引里存的是「展示文本」（保留空格/标点），繁体歌词显示繁体；
        比对用的是另一份归一形态，两者不要混用，否则英文下一句会变成
        「nightswouldallbeempty」这种没有空格的粘连文本。
        """
        return list(self._display.get(song.key, []))

    def known(self, song: Song) -> bool:
        return song.key in self._songs

    def songs(self) -> list[Song]:
        return list(self._meta.values())

    # ------------------------------------------------------------- 落盘
    def dump(self) -> dict:
        songs = []
        for key, song in self._meta.items():
            lines = self._songs.get(key, [])
            if not lines:
                continue
            # 只存展示文本（重灌索引时按变体重建比对形态）
            songs.append(
                {"song": song.to_json(), "lines": self.lines_of(song)[: self._max_store(key)]}
            )
        return {"version": 2, "songs": songs}

    @staticmethod
    def _max_store(key: str) -> int:
        return 400

    def load(self, data: dict) -> None:
        for item in (data or {}).get("songs") or []:
            try:
                song = Song.from_json(item.get("song") or {})
                lines = [str(x) for x in item.get("lines") or []]
            except (TypeError, ValueError):
                continue
            if song.id and lines:
                self.add_song(song, lines)


def _spawn_warm(library: "Library", songs: list[Song]) -> None:
    """后台预拉歌词；没有事件循环时（单测/同步上下文）直接跳过。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        task = asyncio.create_task(library._warm(songs))
        _warm_tasks.add(task)
        task.add_done_callback(_warm_tasks.discard)
    except Exception:  # noqa: BLE001
        pass


_warm_tasks: set = set()


class Library:
    """曲库门面：负责「按歌名找歌 → 拉歌词 → 进索引」和「网易云兜底」。"""

    def __init__(
        self,
        lrclib_base: str,
        timeout: float,
        max_song_lines: int = 400,
        cache_songs: int = 500,
        cache_line_index: int = 200000,
        netease_enabled: bool = True,
    ) -> None:
        self.lrclib = Lrclib(lrclib_base, timeout, max_song_lines)
        self.netease = Netease(timeout) if netease_enabled else None
        self.index = LineIndex(cache_line_index)
        self.cache_songs = max(0, cache_songs)
        self._search_cache: OrderedDict[str, list[Song]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self.stats = {"lrclib": 0, "netease": 0, "index_hit": 0}

    async def close(self) -> None:
        await self.lrclib.close()
        if self.netease is not None:
            await self.netease.close()

    # ------------------------------------------------------------- 搜索缓存
    def _cached(self, q: str) -> Optional[list[Song]]:
        hits = self._search_cache.get(q)
        if hits is not None:
            self._search_cache.move_to_end(q)
        return hits

    def _remember(self, q: str, songs: list[Song]) -> None:
        if self.cache_songs <= 0:
            return
        self._search_cache[q] = songs
        self._search_cache.move_to_end(q)
        while len(self._search_cache) > self.cache_songs:
            self._search_cache.popitem(last=False)

    async def search_songs(self, q: str, limit: int = 3) -> list[Song]:
        q = (q or "").strip()
        if not q:
            return []
        cached = self._cached(q)
        if cached is not None:
            if cached:
                _spawn_warm(self, cached[:2])
            return cached[:limit]
        songs = await self.lrclib.search(q, limit=limit)
        self.stats["lrclib"] += 1
        self._remember(q, songs)
        if songs:
            # 顺手把头几首的歌词拉进索引（异步、不阻塞本次返回）
            _spawn_warm(self, songs[:2])
        return songs

    async def _warm(self, songs: list[Song]) -> None:
        for song in songs:
            try:
                await self.ensure_lyrics(song)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[qqbot_lyric] 预拉歌词失败 {song.label}：{e}")

    async def ensure_lyrics(self, song: Song) -> Optional[list[str]]:
        """确保这首歌的歌词在索引里；返回歌词行（取不到返回 None）。"""
        if self.index.known(song):
            lines = self.index.lines_of(song)
            return lines or None
        lock = self._locks.setdefault(song.key, asyncio.Lock())
        async with lock:
            if self.index.known(song):
                return self.index.lines_of(song) or None
            lines = await self.lrclib.lyrics(song)
            if lines:
                self.index.add_song(song, lines)
                return lines
            return None

    async def netease_lyric_lookup(self, keywords: str, text_norm: str = "") -> Optional[tuple[Song, list[str]]]:
        """按内容搜歌 → 锁定歌名 → 在歌名上精查歌词。

        两阶段的原因：网易云「按内容搜」能容忍模糊片段，但返回的第一首经常是
        翻唱/remix（甚至同名不同歌）；而「按歌名搜」能拿到多首同名版本，
        用它们的一致性校验（:meth:`netease_consensus`）才不会接错。

        可信判定：最佳版本匹配度高，且「多个同名版本都高」或者「歌名本身出现在
        这句里」。只有单首勉强匹配时不算（宁可漏接，不能接错）。
        """
        from .search import similarity

        songs = await self.netease_candidates(keywords)
        if not songs:
            return None
        compact = "".join(ch for ch in keywords.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

        def rank(song: Song) -> float:
            title = clean_title(song.title)
            score = -(0.55 - 0.05 * songs.index(song))
            if text_norm and similarity(title, text_norm) >= 0.5:
                score += 0.4
            if text_norm and title and title in text_norm:
                score += 0.2
            return score

        for song in sorted(songs[:3], key=rank):
            title = clean_title(song.title)
            if not title:
                continue
            consensus = await self.netease_consensus(title, text_norm, keywords)
            if consensus is None:
                continue
            chosen, lines, best, runner = consensus
            title_in_text = bool(text_norm and title and title in text_norm)
            if not title_in_text and not (best >= 0.75 and runner >= 0.6):
                continue
            chosen = Song(id=chosen.id, title=chosen.title, artist=chosen.artist,
                          album=chosen.album, duration=chosen.duration,
                          source="netease", has_lyrics=True, from_library=True)
            self.index.add_song(chosen, lines)  # 进本地索引：下次零网络，也能反向取歌词
            return chosen, lines
        return None

    async def netease_consensus(
        self, title: str, text_norm: str, raw: str = ""
    ) -> Optional[tuple[Song, list[str], float, float]]:
        """在「歌名」已知的前提下，找这句歌词属于哪个版本。

        搜「歌名」拿前几首同名版本，各自取歌词算这句的匹配度：
        返回 ``(最佳版本, 歌词, 最佳分, 次佳分)``。次佳分足够高说明这几个
        同名版本歌词一致（正版/翻唱/伴奏），可信度更高。

        这样既不会被「同名不同歌」骗（《兰亭集序》里也有一句一样的词），
        也不用依赖网易云返回顺序。
        """
        songs = await self.netease_candidates(clean_title(title))
        if not songs:
            return None
        compact = "".join(
            ch for ch in (raw or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
        )
        scored: list[tuple[float, Song, list[str]]] = []
        for song in songs[:3]:
            lines = await self.netease.lyrics(song) if self.netease is not None else None
            if not lines:
                continue
            scored.append((_window_score(text_norm, lines, raw, compact), song, lines))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        best_score, best_song, best_lines = scored[0]
        runner = scored[1][0] if len(scored) > 1 else best_score
        return best_song, best_lines, best_score, runner

    async def netease_candidates(self, keywords: str) -> list[Song]:
        if self.netease is None:
            return []
        songs = await self.netease.search(keywords)
        if songs:
            self.stats["netease"] += 1
        return songs

    async def netease_title_candidates(self, phrase: str, text_norm: str = "") -> list[Song]:
        """按歌名搜网易云，只留「歌名就在这句里」的那几首（点题句）。

        「誰も知らない 二人だけの夜」里的「二人だけの夜」就是歌名：网易云按内容搜
        返回的是《あの夢をなぞって》（歌词里确实有这两句，但歌名不是它），
        按歌名搜才能拿到《二人だけの夜》本身。返回的 Song 已带网易云 id，可直接取歌词。
        """
        from .search import similarity

        if self.netease is None or not phrase:
            return []
        songs = await self.netease.search(phrase)
        out: list[Song] = []
        for song in songs:
            title = clean_title(song.title)
            key = "".join(ch for ch in title.lower() if ch.isalnum() or "\u3040" <= ch <= "\u9fff")
            if not key:
                continue
            # 只留「歌名就是这段字」的（允许歌名比片段短一点，如「二人だけの夜」vs「二人だけの夜に」）
            if key in text_norm or (len(key) >= 3 and similarity(key, text_norm) >= 0.7):
                out.append(song)
        return out[:3]

    async def fetch_by_title(self, title: str) -> list[tuple[Song, list[str]]]:
        """按歌名精确取歌词：搜索结果里标题和原文一致（去后缀后）才算。

        比 ``search_songs`` 更严：模糊搜索「晴天 周杰伦」会返回《周杰伦-晴天》
        《晴天remix》这类，这里只要同名同名的原型，避免把翻唱/remix 的歌词接上来。
        """
        title = clean_title(title)
        if not title:
            return []
        songs = await self.search_songs(title, limit=5)
        out: list[tuple[Song, list[str]]] = []
        for song in songs:
            if clean_title(song.title) != title:
                continue
            lines = await self.ensure_lyrics(song)
            if lines:
                out.append((song, lines))
        # 网上同名的翻唱/remix 版本很多：只留「原型歌手」的几首
        if len(out) > 2:
            named = [p for p in out if p[0].artist and title in f"{p[0].artist}{p[0].title}"]
            out = (named + out)[:2] if named else out[:2]
        return out

    async def netease_lookup(self, keywords: str, text_norm: str = "") -> str:
        """用网易云按内容搜索，返回最可能包含 ``text_norm`` 的歌名（不含歌手）。

        网易云搜索结果里，第一首往往就是答案（它按相关度排序）；但如果带进
        歌词片段一起算，还能把「本来排第一但其实是别的歌」换掉。
        """
        from .search import similarity  # 延迟导入，避免循环

        songs = await self.netease_candidates(keywords)
        if not songs:
            return ""
        best_title, best_score = "", 0.0
        for rank, song in enumerate(songs[:3]):
            title = clean_title(song.title)
            if not title:
                continue
            # 歌名本身出现在片段里 = 强信号（如「小幸运」这种点题词）
            score = 0.55 - 0.05 * rank
            if text_norm and similarity(title, text_norm) >= 0.5:
                score += 0.35
            if len(title) >= 2 and text_norm and title in text_norm:
                score += 0.2
            if score > best_score:
                best_title, best_score = title, score
        return best_title

    async def find_by_name(self, title: str, artist: str = "") -> Optional[Song]:
        """按歌名（+歌手）找一首**带歌词**的歌。"""
        title = clean_title(title)
        if not title:
            return None
        queries = [f"{title} {artist}".strip(), title]
        for q in queries:
            for song in await self.search_songs(q):
                if song.has_lyrics:
                    return song
                lines = await self.ensure_lyrics(song)
                if lines:
                    return song
        return None


def similarity_scores(candidates: str, songs: list[Song]) -> dict[str, float]:
    """模型给出的候选歌名 → 与用户文本的相似度（含标题子串命中加分）。"""
    from .search import similarity  # 避免循环导入

    out: dict[str, float] = {}
    for song in songs:
        out[song.key] = max(
            similarity(candidates, song.title),
            similarity(candidates, clean_title(song.title)),
        )
    return out

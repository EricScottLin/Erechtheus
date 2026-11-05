"""接龙主流程：判定 → 定位 → 接下一句（纯异步逻辑，不直接发消息）。

拆成独立模块的理由：整条链路（闸门 → 模型 → 曲库 → 接龙）可以先在单测里
用假曲库/假模型跑通，再挂到 NoneBot 的事件上，避免把网络和事件耦合在一起。

一次处理的决策顺序：

1. 本地版式闸门：明显不像（太短/太长/事务性口语）直接放弃，不调模型；
2. 正在「连续接」窗口内 → 先只在这首歌里找；找到就接（不再问模型，省时间）；
3. 否则攒批问模型「像不像歌词 + 猜歌名」；
4. 是歌词 → :mod:`search` 定位到具体某首歌某一行 → 返回下一句；
5. 曲库接不上但模型确定是歌词 → 只报歌名（「像是 X - Y 的歌词」），不硬编下一句。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from nonebot.log import logger

from . import gate
from .config import Config
from .gate import Shape, analyze, route
from .judge import LLMJudge, Verdict
from .library import Library, Song
from .search import Hit, SearchContext, best_window, exact_hits, find_in_songs, search
from .state import RelayStore
from .text import Fragment, build_candidates, clean_text, normalize


# 免验证路径超时哨兵：超时 ≠ 不是歌词，要交回模型判定
_TIMEOUT = object()


@dataclass
class Outcome:
    """一次判定 + 接龙的结果。"""

    status: str  # skip / chat / quoted / unmatched / cooldown / replied
    reason: str = ""
    shape: Optional[Shape] = None
    verdict: Optional[Verdict] = None
    hit: Optional[Hit] = None
    title_hint: str = ""
    artist_hint: str = ""
    elapsed: float = 0.0

    @property
    def replied(self) -> bool:
        return self.status == "replied"


def render(hit: Hit, mode: str = "context") -> str:
    """把命中结果渲染成要发出去的文本。

    ``context``：先接下一句，下一行括注「—— 歌名 - 歌手」，像群友在对唱；
    ``plain``：只接下一句。
    """
    body = hit.next_line
    if not body:
        return ""
    if mode == "plain":
        return body
    return f"{body}\n（—— {hit.song.label}）"


def render_title_only(title: str, artist: str) -> str:
    if artist:
        return f"这句像《{title}》的歌词，但我这边只有这个名字，没找到歌里的下一句～"
    return f"这句像歌词，但我没查到是哪首（可能是我曲库里没有）～"


class RelayEngine:
    """按群处理「这条消息是不是歌词、接哪一句」。"""

    def __init__(self, config: Config, library: Library, judge: LLMJudge, store: RelayStore) -> None:
        self.config = config
        self.library = library
        self.judge = judge
        self.store = store
        self._fragments: dict[int, list[Fragment]] = {}
        # 群 -> {"title": 歌名, "artist": 歌手, "ts": 时间}（模型最近一次给的线索）
        self._meta: dict[int, dict] = {}
        self.stats = {"seen": 0, "gated": 0, "judged": 0, "replied": 0}

    # ------------------------------------------------------------- 片段收集
    def add_fragment(self, group_id: int, text: str, user_id: str, ts: float) -> Fragment:
        """把一条消息记进本群最近的片段窗口（用于拼接连续几条）。"""
        frag = Fragment(text=text, norm=normalize(text), user_id=user_id, ts=ts)
        bucket = self._fragments.setdefault(group_id, [])
        bucket.append(frag)
        horizon = max(self.config.qqbot_lyric_attachment_window, self.config.qqbot_lyric_burst_window) + 5
        self._fragments[group_id] = [f for f in bucket if ts - f.ts <= horizon][-16:]
        return frag

    def _candidates(self, group_id: int) -> list[list[Fragment]]:
        bucket = self._fragments.get(group_id) or []
        nearby = bucket[-self.config.qqbot_lyric_max_segments :]
        return build_candidates(
            nearby,
            max_segments=self.config.qqbot_lyric_max_segments,
            burst_window=float(self.config.qqbot_lyric_burst_window),
        )

    def cleanup(self, now: float) -> None:
        """清理过期片段（由定时任务调用，避免内存慢慢涨）。"""
        horizon = max(self.config.qqbot_lyric_attachment_window, self.config.qqbot_lyric_burst_window) + 60
        for gid in list(self._fragments):
            kept = [f for f in self._fragments[gid] if now - f.ts <= horizon]
            if kept:
                self._fragments[gid] = kept
            else:
                self._fragments.pop(gid, None)

    # ------------------------------------------------------------- 主流程
    async def handle(
        self,
        group_id: int,
        text: str,
        user_id: str,
        ts: Optional[float] = None,
        explicit: bool = False,
        recent_fragments: Optional[list[tuple[str, str, float]]] = None,
    ) -> Outcome:
        """处理一条普通文本消息。

        Args:
            group_id: 群号
            text: 候选文本（引用消息时 = 被引用的那条）
            user_id: 发送者
            ts: 时间戳（默认现在）
            explicit: 是否 @机器人 / 引用消息（有意整活，放宽门槛）
            recent_fragments: 最近的消息 ``[(text, user_id, ts)]``（按时间正序，
                不含 ``text`` 本身；测试和「引用消息」场景可以从这里拼候选）
        """
        started = time.monotonic()
        self.stats["seen"] += 1
        now = ts if ts is not None else started

        if explicit and recent_fragments:
            self._seed(group_id, recent_fragments, now)
        frag = self.add_fragment(group_id, text, user_id, now)
        candidates = self._candidates(group_id) or [[frag]]

        # 1) 版式闸门
        scored: list[tuple[list[Fragment], Shape, str]] = []
        for combo in candidates:
            shape = analyze(combo, explicit=explicit)
            decision = route(shape)
            if decision == "skip":
                continue
            scored.append((combo, shape, decision))
        if not scored:
            self.stats["gated"] += 1
            return Outcome(status="skip", reason="版式不像歌词", elapsed=time.monotonic() - started)

        # 2) 连续接窗口：先只看当前这首歌（窗内直接接，接着唱的人不用再等模型）。
        #    注意这里只做**精确/高度相似**的匹配：两条都调模型、都和窗口里的歌不匹配时，
        #    说明群友换歌了，要退回完整判定，不能整群锁死在一首歌上。
        window = float(self.config.qqbot_lyric_relay_window)
        if self.store.in_window(group_id, now, window):
            state = self.store.get(group_id)
            if state.song is not None:
                hit = await self._match_in_song(state.song, scored[-1][0])
                if hit is not None:
                    cooldown = self.store.cooldown_left(
                        group_id, now, float(self.config.qqbot_lyric_cooldown)
                    )
                    if cooldown > 0:
                        return Outcome(status="cooldown", reason=f"冷却 {int(cooldown)}s",
                                       shape=scored[-1][1], hit=hit, verdict=None,
                                       elapsed=time.monotonic() - started)
                    return await self._replied(group_id, user_id, now, hit, None, scored[-1][1], started)

        # 2.5) 免验证路径：版式**很明显**像歌词（中英文都适用）时，先用「按内容搜歌」
        #      当证据 —— 搜不到所属歌曲就直接当聊天，省掉一次模型调用；
        #      搜到了也要曲库逐行比对过才算数（曲库才是真相）。
        latest_combo, latest_shape, latest_decision = scored[-1]
        quick: object = None
        # a) 本地索引里已经有这句歌词（之前接过这首歌）→ 零网络直接接
        local_hit = self._local_exact_hit(latest_combo)
        if local_hit is not None:
            cooldown = self.store.cooldown_left(
                group_id, now, float(self.config.qqbot_lyric_cooldown)
            )
            if cooldown > 0:
                self.store.set_song(group_id, local_hit.song)
                return Outcome(status="cooldown", reason=f"冷却 {int(cooldown)}s",
                               shape=latest_shape, hit=local_hit,
                               elapsed=time.monotonic() - started)
            return await self._replied(
                group_id, user_id, now, local_hit, None, latest_shape, started
            )
        if (
            latest_decision == "obvious"
            and self.config.qqbot_lyric_verify_without_llm
            and getattr(self.library, "netease", None) is not None
        ):
            try:
                quick = await asyncio.wait_for(
                    self._quick_verify(latest_combo),
                    timeout=float(self.config.qqbot_lyric_quick_verify_timeout),
                )
            except asyncio.TimeoutError:
                logger.debug("[qqbot_lyric] 免验证超时，转交模型判定")
                quick = _TIMEOUT
            if quick is None:
                return Outcome(status="chat", reason="版式像歌词但曲库按内容搜不到",
                               shape=latest_shape, elapsed=time.monotonic() - started)
            if quick is not _TIMEOUT:
                quick_hit = quick  # type: ignore[assignment]
                cooldown = self.store.cooldown_left(
                    group_id, now, float(self.config.qqbot_lyric_cooldown)
                )
                if cooldown > 0:
                    self.store.set_song(group_id, quick_hit.song)
                    return Outcome(status="cooldown", reason=f"冷却 {int(cooldown)}s",
                                   shape=latest_shape, hit=quick_hit,
                                   elapsed=time.monotonic() - started)
                return await self._replied(
                    group_id, user_id, now, quick_hit, None, latest_shape, started
                )

        # 3) 问模型（攒批）。只送**最新一条**：几段拼起来会显著拖长模型推理
        #    （实测 5 条拼一段时 90% 概率空回复），定位阶段再按需拼候选。
        judge_texts = [gate_text(scored[-1][1])]
        verdicts = await self.judge.judge_many(judge_texts) if self.config.qqbot_lyric_use_llm else []
        self.stats["judged"] += len(judge_texts)
        best: Optional[tuple[list[Fragment], Shape, Verdict]] = None
        verdict = verdicts[0] if verdicts else None
        if verdict is not None and not verdict.is_lyric:
            kind = verdict.kind.lower()
            return Outcome(
                status="chat" if kind == "chat" else "quoted",
                reason=f"模型判定为 {verdict.kind}",
                shape=scored[-1][1],
                verdict=verdict,
                elapsed=time.monotonic() - started,
            )
        if self.config.qqbot_lyric_use_llm and verdict is not None:
            best = (scored[-1][0], scored[-1][1], verdict)
            if verdict.title:
                self._meta[group_id] = {
                    "title": verdict.title,
                    "artist": verdict.artist,
                    "ts": now,
                }
        if not self.config.qqbot_lyric_use_llm:
            # 没有模型时用版式分兜底：只有明显像的才继续
            combo, shape, decision = scored[-1]
            if decision != "obvious":
                return Outcome(status="skip", reason="无模型且版式不确定", shape=shape,
                               elapsed=time.monotonic() - started)
            best = (combo, shape, None)
        if best is None:
            return Outcome(status="chat", reason="模型判定不是歌词", elapsed=time.monotonic() - started)

        # 定位尝试：先用最长候选，找不到再退回只用这条消息本身。
        # （上下文从「最近几条」里按用户拼，长组合有时会混进无关的话）
        combo, shape, verdict = best
        pinned_song = self.store.pinned_song(group_id, now, float(self.config.qqbot_lyric_same_song_ttl))
        meta = self._meta.get(group_id, {})
        hint_title = (verdict.title if verdict else "") or meta.get("title", "")
        hint_artist = (verdict.artist if verdict else "") or meta.get("artist", "")

        # 4) 曲库定位：先「带上下文的候选」，不行再只用这条消息
        hit = None
        combos = [combo]
        if len(combo) > 1:
            combos.append([frag])
        # 模型确认是歌词但猜不出歌：先用「片段里的显著词」找，别去网易云乱试
        allow_netease = True
        if not hint_title.strip():
            combos = [[frag]]
            allow_netease = False
            # 粘性：上一首刚接过，先在这首歌里找（群友常常接着唱同一首）
            if pinned_song is not None:
                combos = [combo, [frag]] if len(combo) > 1 else [[frag]]
                sticky = await find_in_songs(
                    self.library, [pinned_song],
                    SearchContext.build([frag], recent_song=pinned_song),
                    min_score=self._min_score,
                )
                if sticky is not None and sticky.score >= self._min_score:
                    return await self._replied(group_id, user_id, now, sticky, verdict, shape, started)
        for frags in combos:
            ctx = SearchContext.build(
                frags,
                hint_title=hint_title,
                hint_artist=hint_artist,
                recent_song=pinned_song,
            )
            if pinned_song is not None:
                sub = await find_in_songs(self.library, [pinned_song], ctx, min_score=self._min_score)
                if sub is not None and sub.score >= self._min_score:
                    hit = sub
                    break
            hit = await search(
                self.library, ctx, min_score=self._min_score, allow_netease=allow_netease
            )
            if hit is not None:
                break
        # 证据判定：版式**不明显**像歌词时，不能只信模型说「像某首歌」——
        # 必须由曲库确认（网易云/本地歌词里真的找到这一行）。否则当聊天。
        if (
            hit is not None
            and scored[-1][2] != "obvious"
            and not hit.song.from_library
            and not (pinned_song is not None and hit.song.key == pinned_song.key)
        ):
            logger.debug(
                f"[qqbot_lyric]「{shape.text[:20]}」只是「像某首歌」，版式分不够，不接"
            )
            return Outcome(status="chat", reason="无曲库证据（仅模型推测）", shape=shape,
                           verdict=verdict, elapsed=time.monotonic() - started)
        if hit is None:
            title = hint_title.strip()
            artist = hint_artist.strip()
            return Outcome(
                status="unmatched",
                reason="曲库没有可接的下一句",
                shape=shape,
                verdict=verdict,
                title_hint=title,
                artist_hint=artist,
                elapsed=time.monotonic() - started,
            )

        # 5) 冷却：同一群刚接过就只记状态、不刷屏
        cooldown = self.store.cooldown_left(group_id, now, float(self.config.qqbot_lyric_cooldown))
        if cooldown > 0:
            self.store.set_song(group_id, hit.song)
            return Outcome(status="cooldown", reason=f"冷却 {int(cooldown)}s", shape=shape,
                           verdict=verdict, hit=hit, elapsed=time.monotonic() - started)

        return await self._replied(group_id, user_id, now, hit, verdict, shape, started)

    # ------------------------------------------------------------- 辅助
    @property
    def _min_score(self) -> float:
        return float(self.config.qqbot_lyric_min_score)

    def _local_exact_hit(self, frags: list[Fragment]) -> Optional[Hit]:
        """本地歌词索引里的精确命中（之前接过这首歌就零网络可接）。"""
        if not frags:
            return None
        text = frags[-1].text
        for hit in exact_hits(self.library, text):
            if hit.next_line:
                return hit
        return None

    async def _quick_verify(self, frags: list[Fragment]) -> Optional[Hit]:
        """免验证路径：用「按内容搜歌名」当证据，再在曲库里逐行比对。

        返回命中结果；返回 ``None`` 表示**没有证据**（这句不属于任何歌），
        调用方据此判定「像歌词但不是歌」，直接当聊天处理。
        """
        text_norm = "".join(f.norm for f in frags)
        if not text_norm:
            return None
        # 搜索词用**原文**：英文靠空格分词（"ifihadmylife" 搜不到歌），
        # 中文有没有空格都能搜到。
        keyword = " ".join(f.text for f in frags if f.text).strip()[:60] or text_norm
        try:
            found = await self.library.netease_lyric_lookup(keyword, text_norm)
        except Exception as e:  # noqa: BLE001 —— 免验证路径失败就交给模型
            logger.debug(f"[qqbot_lyric] 免验证搜索失败：{e}")
            return None
        if not found:
            return None
        song, lines = found
        score, start, end = best_window(text_norm, lines, 3)
        if score < self._min_score:
            return None
        return Hit(song=song, score=min(1.0, score), lines=lines, start=start, end=end, how="content")

    async def _match_in_song(self, song: Song, frags: list[Fragment]) -> Optional[Hit]:
        ctx = SearchContext.build(frags, recent_song=song, allow_network=True)
        hit = await find_in_songs(self.library, [song], ctx, min_score=self._min_score)
        if hit is not None and hit.score < self._min_score:
            return None
        return hit

    async def _replied(
        self,
        group_id: int,
        user_id: str,
        now: float,
        hit: Hit,
        verdict: Optional[Verdict],
        shape: Shape,
        started: float,
    ) -> Outcome:
        self.store.mark_replied(group_id, hit.song, user_id, now)
        self.stats["replied"] += 1
        logger.info(
            f"[qqbot_lyric] 群 {group_id} 接龙：{hit.song.label} 第 {hit.start + 1}~{hit.end + 1} 行"
            f"（{hit.how}, score={hit.score:.2f}）"
        )
        return Outcome(
            status="replied",
            shape=shape,
            verdict=verdict,
            hit=hit,
            elapsed=time.monotonic() - started,
        )

    def _seed(self, group_id: int, items: list[tuple[str, str, float]], now: float) -> None:
        window = float(self.config.qqbot_lyric_attachment_window)
        bucket = self._fragments.setdefault(group_id, [])
        existing = {(f.user_id, f.text, round(f.ts, 3)) for f in bucket}
        for text, uid, ts in items:
            if now - ts > window:
                continue
            stamp = (uid, clean_text(text), round(ts, 3))
            if stamp in existing:
                continue
            bucket.append(
                Fragment(text=clean_text(text), norm=normalize(text), user_id=uid, ts=ts)
            )
            existing.add(stamp)
        bucket.sort(key=lambda f: f.ts)
        self._fragments[group_id] = bucket[-16:]


def gate_text(shape: Shape) -> str:
    """给模型看的文本：保留分行，太长时截断。"""
    text = shape.text.strip()
    if len(text) > 120:
        text = text[:117] + "…"
    return text

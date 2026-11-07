"""接龙主流程：判定 → 定位 → 接下一句（纯异步逻辑，不直接发消息）。

拆成独立模块的理由：整条链路（闸门 → 模型 → 曲库 → 接龙）可以先在单测里
用假曲库/假模型跑通，再挂到 NoneBot 的事件上，避免把网络和事件耦合在一起。

一次处理的决策顺序：

1. 本地版式闸门：**当前这条自己**明显不像（太短/太长/事务性口语）直接放弃，
   不调模型、也不拼上下文（否则闲聊会被拼成「像歌词」而误接）；
2. 正在「连续接」窗口内 → 先只在这首歌里找（候选从长到短：拼上上一条才定位得到
   「下一句」）；找到就接（不再问模型，省时间）；只挡「同一句重复发」；
3. 否则攒批问模型「像不像歌词 + 猜歌名」；
4. 是歌词 → :mod:`search` 定位到具体某首歌某一行 → 返回下一句；
5. 曲库接不上但模型确定是歌词 → 只报歌名（「像是 X - Y 的歌词」），不硬编下一句。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import asyncio
import re
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


# 中日文字符（汉字 + 平假名 + 片假名）：保守复核按「字」算覆盖度时用
_CJK_CHAR = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

# 免验证路径超时哨兵：超时 ≠ 不是歌词，要交回模型判定
_TIMEOUT = object()


def _content_chars(text: str) -> str:
    """只留「有内容」的字符（中日韩字 + 英数），标点/空格/emoji 不算。

    「这个好看」= 4 个字，和曲库里那一行一模一样；「？？？」= 0 个字。
    精确命中路径用它判断「短到不足以当歌词」。
    """
    return "".join(ch for ch in (text or "") if ch.isalnum() or _CJK_CHAR.match(ch))


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


def render(hit: Hit, mode: str = "plain") -> str:
    """把命中结果渲染成要发出去的文本。

    ``plain``（默认）：只接下一句，像个群友在对唱 —— **不带歌名**。
    群友要的是接着唱，不是被科普歌名；署名行还会被下一个人复制回来，
    混进歌词里影响匹配。
    ``context``：下一句 + 括注「—— 歌名 - 歌手」（想显摆曲库时用）。
    """
    body = hit.next_line
    if not body:
        return ""
    if mode == "context":
        return f"{body}\n（—— {hit.song.label}）"
    return body


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
        # 群 -> 最近一次判定的原文（给免验证路径做「保守复核」用）
        self._last_raw: dict[int, str] = {}
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

    def reset_group(self, group_id: int) -> None:
        """清空某群的全部上下文（接龙状态 + 片段窗口 + 模型线索）。

        只清 relay 状态是不够的：片段窗口里还留着上一段歌词，会被拼进下一条，
        同一个群连唱两首不同的歌时就会串台。
        """
        self.store.reset(group_id)
        self._fragments.pop(group_id, None)
        self._meta.pop(group_id, None)
        self._last_raw.pop(group_id, None)

    def _candidates(
        self,
        group_id: int,
        current: Optional[Fragment] = None,
        cross_user_window: float = 0.0,
    ) -> list[list[Fragment]]:
        bucket = self._fragments.get(group_id) or []
        nearby = bucket[-self.config.qqbot_lyric_max_segments :]
        combos = build_candidates(
            nearby,
            max_segments=self.config.qqbot_lyric_max_segments,
            burst_window=float(self.config.qqbot_lyric_burst_window),
            cross_user_window=cross_user_window,
        )
        if current is not None:
            # 候选必须以「当前这条」结尾，避免拿旧的片段组合去匹配新消息
            combos = [c for c in combos if c and c[-1].text == current.text]
        return combos

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
        # 连续接龙时，上一条（哪怕是别人发的）是定位「下一句」的关键上下文，
        # 所以窗口内允许跨用户拼接；平时只拼同一个人的连发。
        in_window = self.store.in_window(group_id, now, float(self.config.qqbot_lyric_relay_window))
        cross_window = (
            float(self.config.qqbot_lyric_cross_user_window) if in_window else 0.0
        )
        candidates = self._candidates(group_id, frag, cross_window) or [[frag]]

        # 1) 版式闸门
        #    当前这条**自己**先看一眼：它不像歌词时，拼上下文只能锦上添花，
        #    不能把闲聊拼成「像歌词」（否则「这个bug修好了没 + see you tomorrow」
        #    会拼出一段像歌词的文本，再命中歌里恰好相同的几个字 → 误接）。
        solo_shape = analyze([frag], explicit=explicit)
        solo_ok = route(solo_shape) != "skip"
        scored: list[tuple[list[Fragment], Shape, str]] = []
        if solo_ok:
            scored.append(([frag], solo_shape, route(solo_shape)))
        for combo in candidates:
            if len(combo) == 1 or not solo_ok:
                continue  # [frag] 已经加过了；当前这条不像歌词就不拼上下文
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
        #    候选**从长到短**试：上一条是别人发的上一句时，拼起来才定位得到下一句。
        #    窗内的冷却**不挡接龙**（只挡「同一句重复发」）：连续对唱时第二句就被
        #    冷却吞掉，在群里看着就是「bot 接了一句就不理人了」。
        window = float(self.config.qqbot_lyric_relay_window)
        if in_window:
            state = self.store.get(group_id)
            if state.song is not None:
                # 只试「版式过关」的候选：拼上别人的闲聊凑出一段「像歌词」的文本，
                # 会命中歌里恰好相同的那几个字（「see you tomorrow」真是一句歌词），
                # 那是误接。当前这条自己像歌词时，才允许拼上下文。
                for combo, shape, decision in reversed(scored):
                    if not solo_ok and combo[-1].text != frag.text:
                        continue
                    # 窗口内只接「版式明确像歌词」的：窗内没有模型复核，
                    # 一句闲聊（「思劳牧慢速」）只要碰巧撞上歌里某行就会被接，
                    # 群里看着就是「接歌词的时候乱接别人的话」。
                    if decision != "obvious":
                        continue
                    # 太短的（「这个好看」「真的吗」）一律不接：它们在任何歌里
                    # 都可能是单独一行，撞上的概率远高于「真在唱」。
                    if not self._long_enough(frag):
                        continue
                    hit = await self._match_in_song(state.song, combo)
                    if hit is None:
                        continue
                    # 拼上下文只用来「补上一句」，当前这条自己必须也像歌词：
                    # 否则「Baby my heart + 思劳牧慢速」会命中上一句、再往下接一遍，
                    # 等于把别人的闲聊当歌词接了。
                    if combo[-1] is not frag and not solo_ok:
                        continue
                    # 命中区间必须真的覆盖到当前这条（不能只靠拼上来的上下文命中）。
                    if not self._covers_current(hit, frag):
                        continue
                    # 窗口内只挡「同一句重复发」：连续对唱时每一句都是新的，
                    # 冷却不能挡（否则 bot 接一句就不理人了，看着像「接不上」）。
                    if self._is_repeat(group_id, hit, frag) and self._only_repeats(group_id, combo, hit):
                        return Outcome(status="cooldown", reason="同一句重复发，不刷屏",
                                       shape=shape, hit=hit, verdict=None,
                                       elapsed=time.monotonic() - started)
                    return await self._replied(group_id, user_id, now, hit, None, shape, started)

        # 2.5) 免验证路径（**零 AI**）：只要没被版式判成"明显不像"，就先问曲库——
        #      「按内容搜歌 + 同名版本一致性校验」比问模型便宜得多（0.5~2 秒 vs 2~4 秒），
        #      搜不到就当聊天（不调模型），搜到了也要逐行比对过才算数（曲库才是真相）。
        latest_combo, latest_shape, latest_decision = scored[-1]
        quick: object = None
        self._last_raw[group_id] = " ".join(f.text for f in latest_combo if f.text).strip()
        # a) 本地索引里已经有这句歌词（之前接过这首歌）→ 零网络直接接
        #    同样从长到短试：上一条是别人发的上一句时，拼起来才定位得到下一句。
        local_hit = None
        for combo, _shape, _decision in reversed(scored):
            local_hit = self._local_exact_hit(combo)
            if local_hit is not None:
                break
        if local_hit is not None:
            cooldown = self.store.cooldown_left(
                group_id, now, float(self.config.qqbot_lyric_cooldown), local_hit.song
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
            self.config.qqbot_lyric_verify_without_llm
            and getattr(self.library, "netease", None) is not None
        ):
            try:
                quick = await asyncio.wait_for(
                    self._quick_verify_combos(scored, solo_ok=solo_ok),
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
                    group_id, now, float(self.config.qqbot_lyric_cooldown), quick_hit.song
                )
                if cooldown > 0:
                    self.store.set_song(group_id, quick_hit.song)
                    return Outcome(status="cooldown", reason=f"冷却 {int(cooldown)}s",
                                   shape=latest_shape, hit=quick_hit,
                                   elapsed=time.monotonic() - started)
                return await self._replied(
                    group_id, user_id, now, quick_hit, None, latest_shape, started
                )

        # 3) 模型兜底（默认关闭）。只有在「本地版式分极低」且免验证路径也没找到证据
        #    时才问模型；正常情况完全不需要模型（详见 README 的实测数据）。
        #    只送**最新一条**：几段拼起来会显著拖长模型推理。
        want_llm = (
            self.config.qqbot_lyric_use_llm
            and scored[-1][1].score < float(self.config.qqbot_lyric_llm_fallback_score)
        )
        if not want_llm:
            return Outcome(status="chat", reason="本地无证据（未调模型）",
                           shape=scored[-1][1], elapsed=time.monotonic() - started)
        judge_texts = [gate_text(scored[-1][1])]
        verdicts = await self.judge.judge_many(judge_texts)
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

        # 5) 冷却：同一首歌刚接过就只记状态、不刷屏（换歌/对唱不受影响）
        cooldown = self.store.cooldown_left(
            group_id, now, float(self.config.qqbot_lyric_cooldown), hit.song
        )
        if cooldown > 0:
            self.store.set_song(group_id, hit.song)
            return Outcome(status="cooldown", reason=f"冷却 {int(cooldown)}s", shape=shape,
                           verdict=verdict, hit=hit, elapsed=time.monotonic() - started)

        return await self._replied(group_id, user_id, now, hit, verdict, shape, started)

    # ------------------------------------------------------------- 辅助
    @property
    def _min_score(self) -> float:
        return float(self.config.qqbot_lyric_min_score)

    @staticmethod
    def _covers_current(hit: Hit, frag: Fragment) -> bool:
        """命中区间里的歌词行有没有真的「覆盖当前这条」。

        拼上一条上下文才命中的情况（「Baby my heart + 思劳牧慢速」）里，
        当前这条自己一个字都对不上 → 不能接，否则就是把闲聊当歌词接。
        反过来，一句歌词被拆成歌里的好几行（「And darlin' I will / Be lovin' you /
        Till we're seventy」）时，单行覆盖不到 80%，要把区间里所有行拼起来算。
        """
        current = frag.norm
        if not current:
            return False
        lines = hit.lines[hit.start: hit.end + 1]
        if not lines:
            return False
        for line in lines:
            line_norm = normalize(line)
            if not line_norm:
                continue
            common = sum(1 for ch in current if ch in line_norm)
            if common / max(1, len(current)) >= 0.8:
                return True
        joined = normalize(" ".join(lines))
        if not joined:
            return False
        common = sum(1 for ch in current if ch in joined)
        return common / max(1, len(current)) >= 0.8

    def _only_repeats(self, group_id: int, combo: list[Fragment], hit: Hit) -> bool:
        """当前这条消息是不是「只把刚接过的那句又发了一遍」。

        拼上一条上下文后命中范围可能和上次一样（群友把上一句和新一句一起发），
        但**当前这条**本身是新内容 —— 这种情况要接，不能当重复刷屏。
        """
        if not combo:
            return True
        current = combo[-1].norm
        if not current:
            return True
        for idx in range(hit.start, min(hit.end + 1, len(hit.lines))):
            line = normalize(hit.lines[idx])
            if not line:
                continue
            # 当前这条基本就是这一行（>= 80% 的字都在里面）才算「重复发」；
            # 只是碰巧有几个字相同不算。
            common = sum(1 for ch in current if ch in line)
            if common / max(1, len(current)) >= 0.8:
                return True
        return False

    def _is_repeat(self, group_id: int, hit: Hit, frag: Optional[Fragment] = None) -> bool:
        """这条命中是不是「刚接过的那句」又被发了一遍（防刷屏，不挡对唱）。

        比的是**命中的最后一行**：群友把「上一句 + 新一句」一起发过来时，
        窗口起点可能不变、结尾变长 —— 结尾变了就是新内容，照样要接。

        例外：当前这条就是 bot 刚接的那句（群友复读一遍接着唱，
        「Today's the day I grow some balls」）→ 要往下接，不是刷屏。
        """
        state = self.store.get(group_id)
        if not (state.last_hit_key and state.last_hit_key == f"{hit.song.key}#{hit.end}"):
            return False
        # 复读 bot 刚接的那句 = 接着对唱，要往下接（不是刷屏）。
        # 这里用「用户这条 ↔ 上次 bot 接的那句」判断，不依赖命中区间。
        if frag is not None and self._resung_replied_line(group_id, frag):
            logger.debug("[qqbot_lyric] 复读刚接的那句 → 继续往下接")
            return False
        return True

    def _resung_replied_line(self, group_id: int, frag: Fragment) -> bool:
        """当前这条是不是「bot 刚接的那句」又被发了一遍（复读接着唱）。

        群友复读 bot 刚接的那句（「Today's the day I grow some balls」）是接着对唱，
        要往下接；而把 bot 接过的**上一句**再发一遍（「Baby my heart」）是刷屏 ——
        后者和刚接的那句不是同一行。所以只比「当前这条 ↔ 上次 bot 接的那句」。
        """
        replied = normalize(self.store.get(group_id).last_reply_text or "")
        current = frag.norm
        if not current or not replied:
            return False
        # 比的是「用户这条 ↔ 上次 bot 接的那句」：命中区间起点是用户发的行，
        # 两者一致说明群友复读了 bot 刚接的那句（接着唱），不是刷屏。
        common = sum(1 for ch in current if ch in replied)
        return common / max(1, len(current)) >= 0.8 and common / max(1, len(replied)) >= 0.8

    def _coverage_ok(self, raw: str, line: str, shape_score: float) -> bool:
        """保守复核：用户原句的内容词/字要基本都被命中的那行歌词覆盖。

        这条专门用来挡住「只对上一部分就被接」的误判：
        `i am on my way home` ↔ `I am on my way` 只覆盖了 5/6，
        而歌词片段的空耳/少了标点通常也能到 0.8 以上。
        版式分已经很高（>= obvious 档）的不做这层限制，否则会误伤正常歌词。
        """
        if shape_score >= 0.5:
            return True
        import re as _re

        text = (raw or "").strip()
        if not text:
            return True
        # 英文：词覆盖。两处放宽（都是群友写歌词的常态，不是放水）：
        # - 数字也算词：歌词里是 seventy，群友常写 70；
        # - 允许词形变化：loving ↔ lovin'、darling ↔ darlin'（歌词库常缩写）。
        words = [w for w in _re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 1]
        if words and len(words) >= 2:
            line_words = set(_re.findall(r"[a-z0-9']+", (line or "").lower()))
            covered = sum(1 for w in words if _word_covered(w, line_words))
            return covered / len(words) >= 0.85
        # 中日文：字覆盖（假名也算字 —— 日文歌词「誰も知らない」里假名是关键内容，
        # 只数汉字会得到「二人」这种碎片，覆盖度算出来毫无意义）
        chars = [ch for ch in text if _CJK_CHAR.match(ch)]
        if chars:
            line_chars = set(ch for ch in (line or "") if _CJK_CHAR.match(ch))
            covered = sum(1 for ch in chars if ch in line_chars)
            return covered / len(chars) >= 0.85
        return True

    def _local_exact_hit(self, frags: list[Fragment]) -> Optional[Hit]:
        """本地歌词索引里的精确命中（之前接过这首歌就零网络可接）。

        走这条路时**没有版式分、没有覆盖度复核、也没有模型**，所以必须在这里
        卡最短长度：曲库里满是「这个好看」「为什么」「我知道」这种一行的口语，
        群友正常聊天撞上去就会被接下一句（2026-11-06「这个好看」→「滚」）。
        """
        if not frags:
            return None
        if not self._long_enough(frags[-1]):
            return None
        text = frags[-1].text
        for hit in exact_hits(self.library, text):
            if hit.next_line:
                return hit
        return None

    def _long_enough(self, frag: Fragment) -> bool:
        """这条片段的内容够不够「像一句歌词」（见 ``qqbot_lyric_min_match_chars``）。"""
        limit = int(getattr(self.config, "qqbot_lyric_min_match_chars", 6))
        return len(_content_chars(frag.text)) >= limit

    @staticmethod
    def _solo_combo(
        scored: list[tuple[list[Fragment], Shape, str]], frag: Fragment
    ) -> tuple[list[Fragment], Shape, str]:
        """「当前这条自己」的候选（不拼上下文）。"""
        for item in scored:
            if item[0][-1].text == frag.text:
                return item
        return scored[-1]

    async def _quick_verify_combos(
        self, scored: list[tuple[list[Fragment], Shape, str]], solo_ok: bool = True
    ) -> Optional[Hit]:
        """免验证路径：候选**从长到短**试（拼上上一条才定位得到「下一句」）。

        但只在「当前这条自己像歌词」时才拼上下文：拼上别人的闲聊凑出一段像歌词的
        文本，会命中歌里恰好相同的几个字（「see you tomorrow」真是一句歌词），那是误接。
        """
        if not solo_ok:
            return None
        for combo, shape, _decision in reversed(scored):
            hit = await self._quick_verify(combo, shape.score)
            if hit is not None:
                return hit
        return None

    async def _quick_verify(self, frags: list[Fragment], shape_score: float = 1.0) -> Optional[Hit]:
        """免验证路径：在曲库里找证据（本地索引 → 歌名线索 → 按内容搜），逐行比对。

        返回命中结果；返回 ``None`` 表示**没有证据**（这句不属于任何歌），
        调用方据此判定「像歌词但不是歌」，直接当聊天处理。

        证据来源按「便宜 → 贵」：本地索引（之前接过这首歌就零网络）→ 网易云按内容搜。
        网易云那条要过**保守复核**（用户原句的字/词基本都被命中行覆盖），
        防止「只对上一部分」就接。
        """
        # 多段按行拼（"\n"）：best_window 的「部分命中」靠分行位置判断
        # 「哪几段对上了哪几行」，粘成一坨就只能整段比，容易漏接。
        text_norm = "\n".join(f.norm for f in frags if f.norm)
        if not text_norm:
            return None
        # 搜索词用**原文**：英文靠空格分词（"ifihadmylife" 搜不到歌），
        # 中文有没有空格都能搜到。
        keyword = " ".join(f.text for f in frags if f.text).strip()[:60] or text_norm

        # a) 本地索引里已经有这句（之前接过这首歌）→ 零网络，但也要过保守复核
        #    （「誰も知らない」能被《知らない》里单独一行「知らない」以 0.83 命中，
        #    可这句其实是另一首歌的，只对上一部分不算数）
        #    同样要卡最短长度：短句在曲库里几乎都是口语行，撞上就接等于乱接。
        for frag in reversed(frags):
            if not self._long_enough(frag):
                continue
            entries = self.library.index.lookup(frag.norm)
            for song, idx in entries:
                lines = self.library.index.lines_of(song)
                if not lines or idx >= len(lines):
                    continue
                if not self._coverage_ok(keyword, lines[idx], shape_score):
                    continue
                return Hit(song=song, score=1.0, lines=lines, start=idx, end=idx, how="exact")

        # b) 网易云按内容搜（最慢）+ 保守复核
        try:
            found = await self.library.netease_lyric_lookup(keyword, text_norm)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[qqbot_lyric] 免验证网易云搜索失败：{e}")
            return None
        if not found:
            return None
        song, lines = found
        score, start, end = best_window(text_norm, lines, 3)
        if score < self._min_score:
            return None
        if not self._coverage_ok(keyword, " ".join(lines[start:end + 1]), shape_score):
            logger.debug(
                f"[qqbot_lyric] 保守复核未过（用户原句没被充分覆盖），不接：{lines[start]!r}"
            )
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
        self.store.mark_replied(group_id, hit.song, user_id, now, hit.next_line or "")
        self.store.get(group_id).last_hit_key = f"{hit.song.key}#{hit.end}"
        self.stats["replied"] += 1
        # INFO 级别打一行「接了哪句」：群里「怎么没接上 / 接错了」时，
        # 直接看日志就能知道是没匹配上还是匹配到了别处，不用靠猜。
        logger.info(
            f"[qqbot_lyric] 群 {group_id} 接龙：{hit.song.label} 第 {hit.start + 1}~{hit.end + 1} 行"
            f"（{hit.how}, score={hit.score:.2f}）→ 接「{hit.next_line}」"
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


def _word_covered(word: str, line_words: set[str]) -> bool:
    """这个词是否被歌词行覆盖（允许缩写/词形变化：loving ↔ lovin'）。"""
    if word in line_words:
        return True
    if word.isdigit():
        return False
    stem = word.replace("'", "").rstrip("g") if word.endswith("ng") else word.replace("'", "")
    for other in line_words:
        if other.isdigit():
            continue
        plain = other.replace("'", "")
        if plain.startswith(stem[: max(3, len(stem) - 2)]) or stem.startswith(plain[: max(3, len(plain) - 2)]):
            return True
    return False


def gate_text(shape: Shape) -> str:
    """给模型看的文本：保留分行，太长时截断。"""
    text = shape.text.strip()
    if len(text) > 120:
        text = text[:117] + "…"
    return text

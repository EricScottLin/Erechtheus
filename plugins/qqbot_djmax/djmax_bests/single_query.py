"""单曲查询的数据组装：曲目信息与个人成绩。

只负责「取数 + 归一化成渲染友好的结构」，不画图。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import api_handler
from .models import BMODES, DIFFS, DMSongDBEntry

#: 单曲成绩查询时默认请求的难度顺序（展示用）。
DIFF_ORDER: tuple[str, ...] = DIFFS


@dataclass(frozen=True)
class ChartInfo:
    bmode: int
    diff: str
    level: int
    constant: Decimal
    refined: bool
    rating: int | None
    tags: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.bmode}B {self.diff}"


@dataclass(frozen=True)
class SongInfo:
    songid: int
    title: str
    composer: str
    dlc_code: str
    dlc_name: str
    is_new: bool
    charts: tuple[ChartInfo, ...]
    bmode_chart_count: dict[int, int] = field(default_factory=dict)

    @property
    def artist(self) -> str:
        """仅歌手名；V-Archive 的 composer 也可能含作曲，原样展示即可。"""
        return self.composer or "—"


@dataclass(frozen=True)
class ChartScore:
    bmode: int
    diff: str
    level: int
    constant: Decimal
    score: Decimal | None
    max_combo: bool
    djpower: Decimal | None
    rating: Decimal | None = None
    max_rating: int | None = None
    updated_at: str | None = None
    #: 该谱面是否已有 V-Archive 定数；没有时 ``constant`` 只是游戏内等级。
    refined: bool = True

    @property
    def is_pp(self) -> bool:
        return self.score is not None and self.score >= Decimal("100.00")

    @property
    def state(self) -> str:
        """PP / MC / 空。"""
        if self.score is None:
            return ""
        if self.is_pp:
            return "PP"
        return "MC" if self.max_combo else ""


@dataclass(frozen=True)
class SongScores:
    username: str
    song: SongInfo
    bmode: int
    scores: tuple[ChartScore, ...]

    @property
    def has_any(self) -> bool:
        return any(item.score is not None for item in self.scores)

    @property
    def played_count(self) -> int:
        return sum(1 for item in self.scores if item.score is not None)


def _tag_label(tag) -> str:
    group = (getattr(tag, "tagGroup", "") or "").lower()
    code = (getattr(tag, "tagCode", "") or "").lower()
    if group == "assist" and code == "half":
        return "ASSIST"
    if group == "assist" and code == "max":
        return "ASSIST+"
    if group == "random":
        return "RANDOM"
    if group == "based":
        return "BASED"
    if group == "svam":
        return "SVAM"
    if group == "individual":
        return "INDIVIDUAL"
    return f"{group.upper() or 'TAG'}"


def build_song_info(
    entry: DMSongDBEntry,
    dlc_names: dict[str, str],
    *,
    bmode: int | None = None,
) -> SongInfo:
    """组装单曲信息；``bmode`` 给定时只保留该键数的谱面。

    ``bmode_chart_count`` 只统计过滤后的谱面，渲染层据此决定画哪些键数行。
    """
    charts: list[ChartInfo] = []
    per_bmode: dict[int, int] = {mode: 0 for mode in BMODES}
    for mode, diff, chart in entry.charts():
        if bmode is not None and mode != bmode:
            continue
        per_bmode[mode] += 1
        tags = tuple(dict.fromkeys(_tag_label(t) for t in (chart.tags or [])))
        charts.append(
            ChartInfo(
                bmode=mode,
                diff=diff,
                level=chart.level,
                constant=chart.constant,
                refined=chart.is_refined,
                rating=chart.rating,
                tags=tags,
            )
        )
    return SongInfo(
        songid=entry.songid,
        title=entry.title,
        composer=entry.composer,
        dlc_code=entry.dlc_code,
        dlc_name=dlc_names.get(entry.dlc_code.upper(), entry.dlc_code),
        is_new=entry.is_new,
        charts=tuple(charts),
        bmode_chart_count=per_bmode,
    )


async def fetch_dlc_names() -> dict[str, str]:
    dlc_list = await api_handler.fetch_dlc_list()
    return {
        item.dlcCode.upper(): item.dlcName
        for item in getattr(dlc_list, "root", dlc_list)
    }


async def fetch_song_scores(
    username: str,
    entry: DMSongDBEntry,
    *,
    bmode: int | None = None,
    dlc_names: dict[str, str] | None = None,
) -> SongScores:
    """取该玩家在这首歌上的成绩；返回 ``SongScores``。

    V-Archive 的 archive 接口按「键数」返回该玩家的全部成绩，无法按单曲过滤，
    因此按需要的键数各拉一次再挑出这首歌的谱面。
    """
    modes = (bmode,) if bmode is not None else BMODES
    records = await api_handler.fetch_song_records(username, modes)
    if dlc_names is None:
        dlc_names = await fetch_dlc_names()

    scores: list[ChartScore] = []
    for mode in modes:
        for diff in DIFF_ORDER:
            chart = entry.chart(mode, diff)
            if chart is None:
                continue
            record = records.get((entry.songid, mode, diff))
            scores.append(
                ChartScore(
                    bmode=mode,
                    diff=diff,
                    level=chart.level,
                    constant=chart.constant,
                    score=record.score if record else None,
                    max_combo=record.max_combo if record else False,
                    djpower=record.djpower if record else None,
                    rating=record.rating if record else None,
                    max_rating=record.max_rating if record else None,
                    updated_at=record.updated_at if record else None,
                    refined=chart.is_refined,
                )
            )
    song = build_song_info(entry, dlc_names, bmode=bmode)
    return SongScores(username=username, song=song, bmode=bmode or 0, scores=tuple(scores))

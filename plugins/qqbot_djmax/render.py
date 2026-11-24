"""单曲卡渲染（Pillow）。

字体使用容器内的 Noto Sans CJK；找不到时退回 PIL 默认字体，
保证在缺字体的环境下也能出图而不是抛异常。

配色沿用 ``djmax_bests.constants`` 的键数色 / 难度色 / 曲包色。
"""
from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .djmax_bests import api_handler, constants
from .djmax_bests.single_query import ChartInfo, SongInfo, SongScores

#: 候选字体，按顺序取第一个存在的。优先用素材包里的 Respect（与分表图一致），
#: 其次退到系统 CJK 字体，保证缺素材的环境也能出图。
_SYSTEM_FONTS = {
    "bd": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    "rg": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
}

BG = (37, 37, 37)
PANEL = (48, 48, 48)
PANEL_ALT = (42, 42, 42)
TEXT = (255, 255, 255)
TEXT_DIM = (168, 168, 168)
TEXT_FAINT = (118, 118, 118)
WHITE_SOFT = (232, 232, 232)
ACCENT = (240, 190, 64)
PANEL_LINE = (70, 70, 70)

INFO_WIDTH = 1400
INFO_COVER = 360
INFO_PAD = 46
SCORE_WIDTH = 1400
SCORE_COVER = 200

BMODES_ORDER = (4, 5, 6, 8)
DIFFS_ORDER = ("NM", "HD", "MX", "SC")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _assets_font(kind: str) -> str:
    """素材包里的 Respect 字体路径；素材未就绪时返回空串。"""
    try:
        from .assets import assets_root

        root = assets_root()
    except Exception:
        return ""
    if root is None:
        return ""
    path = os.path.join(str(root), "fonts", f"Respect_{kind}.ttf")
    return path if os.path.exists(path) else ""


def _font_path(kind: str) -> str | None:
    # 不缓存：素材可能在插件加载之后才下载完成。
    preferred = _assets_font(kind)
    if preferred:
        return preferred
    for candidate in _SYSTEM_FONTS[kind]:
        if os.path.exists(candidate):
            return candidate
    return None


@lru_cache(maxsize=64)
def _font(kind: str, size: int):
    path = _font_path(kind)
    if path is None:
        return ImageFont.load_default()
    try:
        # .ttc 需要指定 index；Noto Sans CJK 的简体中文在 index 2。
        if path.endswith(".ttc"):
            return ImageFont.truetype(path, size, index=2)
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _bd(size: int):
    return _font("bd", size)


def _rg(size: int):
    return _font("rg", size)


def _fit(font, text: str, limit: int) -> str:
    """按像素宽度截断，超出加省略号。"""
    if font.getlength(text) <= limit:
        return text
    while text and font.getlength(text + "…") > limit:
        text = text[:-1]
    return text + "…"


def _bmode_color(bmode: int) -> tuple[int, int, int]:
    return _hex_to_rgb(constants.BMODE_COLOR.get(bmode, "#ffffff"))


def _diff_color(diff: str, constant: float = 0.0) -> tuple[int, int, int]:
    """难度色；SC 按定数分三档（SC0/SC1/SC2），其余难度一档。"""
    key = diff.upper()
    if key == "SC":
        # 与 djmax_bests 的 DIFF_COLOR 一致：SC 按定数细分三档
        if constant >= 13:
            key = "SC2"
        elif constant >= 10:
            key = "SC1"
        else:
            key = "SC0"
    return _hex_to_rgb(constants.DIFF_COLOR.get(key) or "#ffffff")


def _dlc_color(code: str) -> tuple[int, int, int]:
    return _hex_to_rgb(constants.DLC_COLOR.get((code or "").upper(), "#9e9e9e"))


def _chrome(width: int, height: int, cover: Image.Image | None) -> Image.Image:
    """深色底 + 曲绘模糊氛围 + 顶部强调线。"""
    card = Image.new("RGB", (width, height), BG)
    if cover is not None:
        # 放大裁切后模糊，铺满整张卡当背景，再压暗。
        scale = max(width / cover.width, height / cover.height)
        backdrop = cover.resize(
            (max(1, int(cover.width * scale)), max(1, int(cover.height * scale))),
            Image.LANCZOS,
        )
        left = max(0, (backdrop.width - width) // 2)
        top = max(0, (backdrop.height - height) // 2)
        backdrop = backdrop.crop((left, top, left + width, top + height))
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(28))
        shade = Image.new("RGB", (width, height), BG)
        card = Image.blend(backdrop, shade, 0.82)
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, width, 5), fill=ACCENT)
    return card


async def _cover_tile(songid: int, size: int) -> Image.Image | None:
    """取曲绘并缩放到指定边长；失败返回 None，渲染回退到纯色底。"""
    try:
        cover = await api_handler.get_cover(songid)
    except Exception:
        return None
    if cover is None:
        return None
    cover = cover.convert("RGB")
    if cover.width != cover.height:
        side = min(cover.width, cover.height)
        left = (cover.width - side) // 2
        top = (cover.height - side) // 2
        cover = cover.crop((left, top, left + side, top + side))
    return cover.resize((size, size), Image.LANCZOS)


# --- 曲目信息卡 ---------------------------------------------------------------


def _draw_info_sync(song: SongInfo, cover: Image.Image | None) -> Image.Image:
    rows = [b for b in BMODES_ORDER if song.bmode_chart_count.get(b)] or list(BMODES_ORDER)

    top_h = INFO_COVER + INFO_PAD * 2
    grid_head_h = 56
    row_h = 88
    grid_top = top_h + 16
    footer_h = 74
    height = grid_top + grid_head_h + len(rows) * row_h + footer_h

    card = _chrome(INFO_WIDTH, height, cover)
    draw = ImageDraw.Draw(card)

    # 左侧曲绘
    if cover is not None:
        card.paste(cover, (INFO_PAD, INFO_PAD))
    else:
        draw.rectangle(
            (INFO_PAD, INFO_PAD, INFO_PAD + INFO_COVER, INFO_PAD + INFO_COVER),
            fill=PANEL,
        )
        draw.text(
            (INFO_PAD + INFO_COVER // 2, INFO_PAD + INFO_COVER // 2),
            "NO COVER",
            font=_rg(28),
            fill=TEXT_FAINT,
            anchor="mm",
        )
    draw.rectangle(
        (INFO_PAD - 2, INFO_PAD - 2, INFO_PAD + INFO_COVER + 1, INFO_PAD + INFO_COVER + 1),
        outline=(96, 96, 96),
        width=2,
    )

    # 右侧标题区
    text_x = INFO_PAD + INFO_COVER + 38
    text_w = INFO_WIDTH - text_x - INFO_PAD
    base_y = INFO_PAD

    draw.text((text_x, base_y), "DJMAX RESPECT V", font=_bd(26), fill=TEXT_DIM)
    draw.text((text_x, base_y + 38), _fit(_bd(64), song.title, text_w), font=_bd(64), fill=TEXT)

    draw.text((text_x, base_y + 130), "ARTIST", font=_bd(22), fill=TEXT_FAINT)
    draw.text(
        (text_x + 104, base_y + 122),
        _fit(_rg(40), song.artist or "—", text_w - 104),
        font=_rg(40),
        fill=WHITE_SOFT,
    )

    # 曲包徽标 + songid
    pill_y = base_y + 194
    pill_h = 54
    dlc_label = song.dlc_code or "?"
    dlc_w = int(_bd(30).getlength(dlc_label)) + 38
    draw.rounded_rectangle(
        (text_x, pill_y, text_x + dlc_w, pill_y + pill_h),
        radius=6,
        fill=_dlc_color(song.dlc_code),
    )
    draw.text(
        (text_x + dlc_w // 2, pill_y + pill_h // 2 + 2),
        dlc_label,
        font=_bd(30),
        fill=(20, 20, 20),
        anchor="mm",
    )
    draw.text(
        (text_x + dlc_w + 16, pill_y + pill_h // 2),
        _fit(_rg(34), song.dlc_name, text_w - dlc_w - 210),
        font=_rg(34),
        fill=TEXT_DIM,
        anchor="lm",
    )
    draw.text(
        (text_x + text_w, pill_y + pill_h // 2),
        f"#{song.songid}",
        font=_bd(30),
        fill=TEXT_FAINT,
        anchor="rm",
    )

    detail_y = pill_y + pill_h + 24
    chart_total = sum(song.bmode_chart_count.values())
    draw.text(
        (text_x, detail_y),
        f"{chart_total} 张谱面 · {len(rows)} 个键数",
        font=_rg(28),
        fill=TEXT_FAINT,
    )
    if song.is_new:
        bw = int(_bd(26).getlength("NEW")) + 28
        bx = text_x + text_w
        draw.rounded_rectangle((bx - bw, detail_y - 6, bx, detail_y + 38), radius=5, fill=(205, 40, 55))
        draw.text((bx - bw // 2, detail_y + 18), "NEW", font=_bd(26), fill=TEXT, anchor="mm")

    # 定数矩阵
    label_w = 150
    col_w = (INFO_WIDTH - INFO_PAD * 2 - label_w) // len(DIFFS_ORDER)
    grid_x = INFO_PAD

    for i, diff in enumerate(DIFFS_ORDER):
        cx = grid_x + label_w + col_w * i + col_w // 2
        draw.text((cx, grid_top), diff, font=_bd(30), fill=_diff_color(diff, 0), anchor="mt")
    draw.line(
        (grid_x, grid_top + grid_head_h - 10, INFO_WIDTH - INFO_PAD, grid_top + grid_head_h - 10),
        fill=PANEL_LINE,
        width=2,
    )

    by_chart: dict[tuple[int, str], ChartInfo] = {(c.bmode, c.diff): c for c in song.charts}
    for row, bmode in enumerate(rows):
        y = grid_top + grid_head_h + row * row_h
        if row % 2 == 0:
            draw.rectangle(
                (grid_x - 6, y - 6, INFO_WIDTH - INFO_PAD + 6, y + row_h - 14),
                fill=PANEL_ALT,
            )
        draw.rectangle((grid_x - 6, y + 6, grid_x + 4, y + row_h - 34), fill=_bmode_color(bmode))
        draw.text(
            (grid_x + 18, y + row_h // 2 - 12),
            f"{bmode}B",
            font=_bd(40),
            fill=_bmode_color(bmode),
            anchor="lm",
        )
        for i, diff in enumerate(DIFFS_ORDER):
            cx = grid_x + label_w + col_w * i + col_w // 2
            cy = y + row_h // 2 - 12
            chart = by_chart.get((bmode, diff))
            if chart is None:
                draw.text((cx, cy), "—", font=_rg(32), fill=TEXT_FAINT, anchor="mm")
                continue
            color = _diff_color(diff, float(chart.constant))
            if chart.refined:
                draw.text((cx, cy - 6), f"{chart.constant:g}", font=_bd(42), fill=color, anchor="mm")
            else:
                draw.text((cx, cy - 6), f"{chart.constant:g}", font=_bd(32), fill=TEXT_DIM, anchor="mm")
            if chart.tags:
                draw.text(
                    (cx, cy + 26),
                    "/".join(chart.tags[:2]),
                    font=_rg(18),
                    fill=TEXT_FAINT,
                    anchor="mt",
                )

    draw.text(
        (INFO_PAD, height - footer_h // 2 - 6),
        "定数来自 V-Archive；未上榜单谱显示整数等级",
        font=_rg(24),
        fill=TEXT_FAINT,
        anchor="lm",
    )
    return card.convert("RGB")


async def render_song_info(song: SongInfo) -> Image.Image:
    cover = await _cover_tile(song.songid, INFO_COVER)
    return await asyncio.to_thread(_draw_info_sync, song, cover)


# --- 单曲成绩卡 ---------------------------------------------------------------


def _draw_scores_sync(scores: SongScores, cover: Image.Image | None) -> Image.Image:
    rows = list(scores.scores)
    # 数据区从封面下方开始：封面占了 INFO_PAD ~ INFO_PAD+SCORE_COVER，
    # 表头紧贴数据区上方，不能压到封面。
    head_h = INFO_PAD + SCORE_COVER + 46
    row_h = 92
    height = head_h + max(1, len(rows)) * row_h + 96

    card = _chrome(SCORE_WIDTH, height, cover)
    draw = ImageDraw.Draw(card)

    if cover is not None:
        card.paste(cover, (INFO_PAD, INFO_PAD))
    else:
        draw.rectangle(
            (INFO_PAD, INFO_PAD, INFO_PAD + SCORE_COVER, INFO_PAD + SCORE_COVER),
            fill=PANEL,
        )
    draw.rectangle(
        (
            INFO_PAD - 2,
            INFO_PAD - 2,
            INFO_PAD + SCORE_COVER + 1,
            INFO_PAD + SCORE_COVER + 1,
        ),
        outline=(96, 96, 96),
        width=2,
    )

    song = scores.song
    text_x = INFO_PAD + SCORE_COVER + 34
    text_w = SCORE_WIDTH - text_x - INFO_PAD

    draw.text((text_x, INFO_PAD), f"{scores.bmode}B 单曲成绩", font=_bd(24), fill=TEXT_DIM)
    draw.text(
        (text_x, INFO_PAD + 34),
        _fit(_bd(48), song.title, text_w),
        font=_bd(48),
        fill=TEXT,
    )
    draw.text(
        (text_x, INFO_PAD + 102),
        _fit(_rg(30), song.artist or "—", text_w),
        font=_rg(30),
        fill=TEXT_DIM,
    )
    draw.text(
        (text_x, INFO_PAD + 148),
        f"玩家 {scores.username} · 已玩 {scores.played_count}/{len(rows)}",
        font=_rg(26),
        fill=TEXT_FAINT,
    )

    # 各难度一行；列宽固定，表头与数据行共用同一套坐标
    col_diff_x = INFO_PAD + 6
    col_level_x = INFO_PAD + 190
    col_const_x = INFO_PAD + 340
    col_score_x = INFO_PAD + 560
    col_power_x = SCORE_WIDTH - INFO_PAD - 250
    col_badge_x = SCORE_WIDTH - INFO_PAD - 30

    # 表头：说明每列是什么
    head_y = head_h - 58
    head_font = _bd(22)
    for text, x, anchor in (
        ("难度", col_diff_x + 52, "mm"),
        ("等级", col_level_x, "lm"),
        ("定数", col_const_x, "lm"),
        ("成绩", col_score_x, "lm"),
        ("DJPower", col_power_x, "lm"),
    ):
        draw.text((x, head_y), text, font=head_font, fill=TEXT_FAINT, anchor=anchor)
    draw.line(
        (INFO_PAD - 6, head_y + 26, SCORE_WIDTH - INFO_PAD + 6, head_y + 26),
        fill=PANEL_LINE,
        width=2,
    )

    grid_top = head_h
    for row, item in enumerate(rows):
        y = grid_top + row * row_h
        if row % 2 == 0:
            draw.rectangle((INFO_PAD - 6, y - 6, SCORE_WIDTH - INFO_PAD + 6, y + row_h - 16), fill=PANEL_ALT)
        center_y = y + row_h // 2 - 14

        # 难度胶囊
        color = _diff_color(item.diff, float(item.constant))
        pill_w, pill_h = 104, 46
        draw.rounded_rectangle(
            (col_diff_x, center_y - pill_h // 2, col_diff_x + pill_w, center_y + pill_h // 2),
            radius=6,
            fill=color if item.score is not None else PANEL,
        )
        draw.text(
            (col_diff_x + pill_w // 2, center_y),
            item.diff,
            font=_bd(28),
            fill=(18, 18, 18) if item.score is not None else TEXT_FAINT,
            anchor="mm",
        )

        # 游戏内等级（1~15）
        draw.text(
            (col_level_x, center_y),
            str(item.level),
            font=_rg(30),
            fill=TEXT_DIM,
            anchor="lm",
        )

        # 定数：没有定位时显示「—」，避免把等级误读成定数
        if item.refined:
            draw.text(
                (col_const_x, center_y),
                f"{item.constant:g}",
                font=_bd(32),
                fill=TEXT,
                anchor="lm",
            )
        else:
            draw.text((col_const_x, center_y), "—", font=_rg(30), fill=TEXT_FAINT, anchor="lm")

        # 分数
        if item.score is None:
            draw.text((col_score_x, center_y), "未游玩", font=_rg(32), fill=TEXT_FAINT, anchor="lm")
            continue
        draw.text((col_score_x, center_y), f"{item.score:.2f}%", font=_bd(38), fill=TEXT, anchor="lm")

        # 状态徽章
        if item.state:
            badge_color = ACCENT if item.state == "PP" else (255, 214, 102)
            bw = int(_bd(24).getlength(item.state)) + 28
            draw.rounded_rectangle(
                (col_badge_x - bw, center_y - 20, col_badge_x, center_y + 20),
                radius=5,
                fill=badge_color,
            )
            draw.text(
                (col_badge_x - bw // 2, center_y),
                item.state,
                font=_bd(24),
                fill=(18, 18, 18),
                anchor="mm",
            )

        # DJPower
        if item.djpower is not None:
            draw.text(
                (col_power_x, center_y),
                f"{item.djpower:.4f}",
                font=_bd(30),
                fill=ACCENT,
                anchor="lm",
            )

    best = max((s.djpower for s in rows if s.djpower is not None), default=None)
    footer = "等级为游戏内显示难度，定数为 V-Archive 非官方定数；「—」表示该谱面暂无定数"
    if best is not None:
        footer += f" · 本键数最高 DJPower {best:.4f}"
    draw.text(
        (INFO_PAD, height - 48),
        footer,
        font=_rg(22),
        fill=TEXT_FAINT,
        anchor="lm",
    )
    return card.convert("RGB")


async def render_song_scores(scores: SongScores) -> Image.Image:
    cover = await _cover_tile(scores.song.songid, SCORE_COVER)
    return await asyncio.to_thread(_draw_scores_sync, scores, cover)


def to_png(image: Image.Image) -> bytes:
    """编码成 WebP。

    分表长图动辄 3000×4700，PNG 能有 7MB，QQ 端容易发送失败或超时；
    WebP 无损视觉差异、体积约为 PNG 的三成。单曲卡两种格式都小，统一走这里。
    """
    buffer = BytesIO()
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    image.save(buffer, format="WEBP", quality=90, method=4)
    return buffer.getvalue()

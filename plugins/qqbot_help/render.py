"""帮助菜单出图（PIL）：总览 + 功能详情。

版式沿用已确认的样式：深色底、块 → 功能 → 主要命令；详情页是
「块 › 功能」+ 开关状态 + 命令分组（左侧命令、右侧说明/价格）。

价格用月币结算，图标是这里现画的月牙（字体不含 emoji 字形，
直接写 🌙 会变成方框，所以不用 emoji）。
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .menu import BLOCK_COLORS, PRICE_UNIT, Feature

BG = (13, 17, 23)
CARD = (22, 28, 38, 255)
GOLD = (245, 196, 81)
FREE = (120, 130, 145)
DESC = (140, 150, 165)
DIM = (85, 95, 110)

_FONT_NAMES = {
    "regular": "ResourceHanRoundedCN-Bold.ttf",
    "mono": "MapleMono-NF-CN-SemiBold.ttf",
}
_FONT_DIRS = (
    Path("/static/font"),                              # 容器内（maimaidx-static）
    Path("/srv/qqbot/maimaidx-static/static/font"),    # 宿主机
    Path(__file__).resolve().parents[2] / "maimaidx-static" / "static" / "font",
    Path("/app/data/helppreview"),                     # 兜底：历史预览目录
)


def _font_dir() -> Path | None:
    for folder in _FONT_DIRS:
        if (folder / _FONT_NAMES["regular"]).is_file():
            return folder
    return None


def available() -> bool:
    """字体齐全才出图，否则调用方退回文字帮助。"""
    return _font_dir() is not None


@lru_cache(maxsize=32)
def _font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    folder = _font_dir()
    if folder is None:
        return ImageFont.load_default()
    return ImageFont.truetype(str(folder / _FONT_NAMES["mono" if mono else "regular"]), size)


def _hexc(color: str) -> tuple[int, int, int]:
    return tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    if _tw(draw, text, font) <= width:
        return text
    while text and _tw(draw, text + "…", font) > width:
        text = text[:-1]
    return text + "…"


@lru_cache(maxsize=8)
def _moon_mask(size: int) -> Image.Image:
    """月牙图标（L 通道 mask），供价格列 paste 使用。"""
    scale = 4
    s = size * scale
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([0, 0, s - 1, s - 1], fill=255)          # 外圆
    md.ellipse([int(s * 0.43), int(-s * 0.05),           # 偏移内圆挖出月牙
                int(s * 2.33), int(s * 1.85)], fill=0)
    return mask.resize((size, size), Image.LANCZOS)


def _price_width(draw: ImageDraw.ImageDraw, price: int) -> float:
    if price <= 0:
        return _tw(draw, "免费", _font(15))
    return _tw(draw, f"{price} {PRICE_UNIT}", _font(15)) + 18


def _draw_price(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                right: float, y: float, price: int) -> None:
    """右对齐画价格：免费 = 灰字；收费 = 月牙图标 + 「100 月币」。"""
    if price <= 0:
        label = "免费"
        draw.text((right - _tw(draw, label, _font(15)), y), label,
                  font=_font(15), fill=FREE)
        return
    label = f"{price} {PRICE_UNIT}"
    width = _tw(draw, label, _font(15))
    x = right - width
    icon = Image.new("RGB", (13, 13), GOLD)
    canvas.paste(icon, (int(x - 17), int(y + 4)), _moon_mask(13))
    draw.text((x, y), label, font=_font(15), fill=GOLD)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------- 总览
def render_overview(blocks: list[tuple[str, list[tuple[str, tuple[str, ...]]]]],
                    footer: str) -> bytes:
    """blocks: [(块名, [(功能名, (主要命令...)), ...]), ...]。"""
    width, pad, row_h, gap = 750, 32, 40, 8
    layout, y = [], 140
    for bname, feats in blocks:
        layout.append((y, "block", bname, None))
        y += 44
        for fname, cmds in feats:
            layout.append((y, "row", fname, cmds))
            y += row_h
        y += gap + 14
    y += 18
    height = y + 44

    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.text((pad, 40), "命令总览", font=_font(38), fill=(255, 255, 255))
    draw.text((pad + 2, 88), "只显示本群已开启的功能 · 发 /help 功能名 看全部指令",
              font=_font(17), fill=(120, 130, 145))

    split = 300
    for index, item in enumerate(layout):
        y, kind, name, cmds = item
        if kind == "block":
            col = _hexc(BLOCK_COLORS.get(name, "#94a3b8"))
            draw.rounded_rectangle([pad, y + 10, pad + 5, y + 36], radius=2, fill=col + (255,))
            draw.text((pad + 16, y + 6), name, font=_font(26), fill=col)
            x2 = pad + 16 + _tw(draw, name, _font(26)) + 14
            draw.line([(x2, y + 23), (width - pad, y + 23)], fill=col + (45,), width=2)
            continue
        if index % 2 == 0:
            draw.rectangle([pad, y, width - pad, y + row_h], fill=(255, 255, 255, 6))
        draw.text((pad + 12, y + 8), _fit(draw, name, _font(20), split - pad - 20),
                  font=_font(20), fill=(238, 242, 246))
        x_right = width - pad - 12
        for cmd in reversed(cmds[:3]):
            cmd_w = _tw(draw, cmd, _font(18, True))
            draw.text((x_right - cmd_w, y + 9), cmd, font=_font(18, True),
                      fill=_hexc(BLOCK_COLORS.get(name, "#94a3b8")))
            x_right -= cmd_w + 16

    draw.text((pad, height - 40), footer, font=_font(15), fill=DIM)
    return _png(canvas)


# ---------------------------------------------------------------- 详情
def render_detail(feature: Feature, tag: str | None = None) -> bytes:
    """feature 的详情页。

    tag：顶部状态标签，调用方决定写什么 ——
    - 受 /开启 xx 控制（有群开关的功能）
    - 本群未受开关控制 · 常开（没有开关的功能）
    - None：不画标签（科技这类，不暴露控制方式）
    """
    width, pad = 750, 32
    col = _hexc(BLOCK_COLORS.get(feature.block, "#94a3b8"))

    has_price = any(cmd.price > 0 for _, cmds in feature.groups for cmd in cmds)

    rows, y = [], 168 if tag is None else 196
    for gname, cmds in feature.groups:
        rows.append(("group", y, gname))
        y += 42
        for cmd in cmds:
            rows.append(("cmd", y, cmd))
            y += 52
        y += 10
    y += 12
    note_start = y
    if feature.notes:
        y += 46 + len(feature.notes) * 30
    height = y + 34

    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas, "RGBA")

    draw.text((pad, 40), feature.block, font=_font(22), fill=(120, 130, 145))
    bx = pad + _tw(draw, feature.block, _font(22)) + 10
    draw.text((bx, 38), "›", font=_font(22), fill=(90, 100, 115))
    draw.text((bx + 22, 32), feature.name, font=_font(40), fill=col)

    if tag:
        lw = _tw(draw, tag, _font(16)) + 24
        draw.rounded_rectangle([pad, 96, pad + lw, 126], radius=15,
                               fill=(60, 60, 70, 90), outline=col + (90,), width=1)
        draw.text((pad + 12, 101), tag, font=_font(16), fill=(150, 155, 165))

    desc_y = 146 if tag else 108
    draw.text((pad, desc_y), _fit(draw, feature.desc, _font(17), width - pad * 2),
              font=_font(17), fill=(135, 145, 160))

    price_right = width - pad - 14
    for row in rows:
        if row[0] == "group":
            _, ry, gname = row
            draw.text((pad, ry + 6), gname, font=_font(20), fill=col)
            x2 = pad + _tw(draw, gname, _font(20)) + 12
            draw.line([(x2, ry + 18), (width - pad, ry + 18)], fill=col + (40,), width=1)
            continue
        _, ry, cmd = row
        draw.rounded_rectangle([pad, ry, width - pad, ry + 44], radius=10, fill=CARD)
        draw.text((pad + 14, ry + 11), cmd.cmd, font=_font(19, True), fill=col)
        x = pad + 14 + _tw(draw, cmd.cmd, _font(19, True)) + 14
        right = price_right
        if has_price:
            _draw_price(canvas, draw, right, ry + 13, cmd.price)
            right -= _price_width(draw, cmd.price) + 10
        avail = right - x - 6
        if cmd.desc and avail > 60:
            draw.text((x, ry + 13), _fit(draw, cmd.desc, _font(16), avail),
                      font=_font(16), fill=DESC)

    if feature.notes:
        draw.text((pad, note_start), "提示", font=_font(20), fill=(150, 160, 175))
        ny = note_start + 34
        for note in feature.notes:
            draw.ellipse([pad + 2, ny + 8, pad + 8, ny + 14], fill=(90, 100, 115))
            draw.text((pad + 18, ny), _fit(draw, note, _font(16), width - pad * 2 - 24),
                      font=_font(16), fill=(120, 130, 145))
            ny += 30

    draw.text((pad, height - 40), "返回总览：/help     •     其他功能：/help 功能名",
              font=_font(15), fill=DIM)
    return _png(canvas)

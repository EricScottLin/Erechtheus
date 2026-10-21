"""表情库总览拼图：把一页表情包拼成一张图，方便群里一次看完。"""

from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image, ImageDraw

from .card import _font
from .memes import meme_path

COLS = 6
THUMB = 170
GAP = 10
PAD = 20
LABEL_H = 24
HEADER_H = 62

BG = (247, 244, 238, 255)
CARD = (255, 255, 255, 255)
CARD_BORDER = (228, 221, 208, 255)
TEXT = (60, 54, 48, 255)
MUTED = (140, 132, 120, 255)


def _cell_image(path) -> Image.Image | None:
    try:
        im = Image.open(path)
        im.seek(0)  # GIF 只取第一帧
        return im.convert("RGBA")
    except Exception:
        return None


def render_meme_sheet(
    items: list[dict[str, Any]],
    *,
    total: int = 0,
    page: int = 1,
    pages: int = 1,
    cols: int = COLS,
    thumb: int = THUMB,
) -> bytes:
    """把 items 拼成一张总览图，返回 PNG bytes。"""
    rows = max(1, math.ceil(len(items) / cols))
    width = PAD * 2 + cols * thumb + (cols - 1) * GAP
    height = HEADER_H + PAD + rows * (thumb + LABEL_H + GAP) - GAP + PAD
    canvas = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(canvas)

    title_font = _font(26, cn=True)
    sub_font = _font(15, cn=True)
    label_font = _font(15, cn=True)

    draw.text((PAD, 16), "PIG MEMES", font=title_font, fill=TEXT)
    sub = f"共 {total or len(items)} 张"
    if pages > 1:
        sub += f" · 第 {page}/{pages} 页"
    draw.text((PAD + 152, 24), sub, font=sub_font, fill=MUTED)
    draw.line(
        [(PAD, HEADER_H - 12), (width - PAD, HEADER_H - 12)],
        fill=CARD_BORDER,
        width=1,
    )

    for idx, item in enumerate(items):
        col = idx % cols
        row = idx // cols
        x = PAD + col * (thumb + GAP)
        y = HEADER_H + PAD + row * (thumb + LABEL_H + GAP)
        draw.rounded_rectangle(
            (x, y, x + thumb, y + thumb),
            radius=12,
            fill=CARD,
            outline=CARD_BORDER,
            width=1,
        )
        img = _cell_image(meme_path(item))
        if img is not None:
            img.thumbnail((thumb - 12, thumb - 12), Image.LANCZOS)
            ox = x + (thumb - img.width) // 2
            oy = y + (thumb - img.height) // 2
            canvas.paste(img, (ox, oy), img)
        label = f"#{item.get('id')}"
        tw = draw.textlength(label, font=label_font)
        draw.text(
            (x + (thumb - tw) / 2, y + thumb + 3),
            label,
            font=label_font,
            fill=MUTED,
        )

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()

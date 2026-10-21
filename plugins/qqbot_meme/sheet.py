"""表情库总览拼图：把本地/来源表情拼成一张带序号的大图，避免刷屏。"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

COLS = 6
THUMB = 170
GAP = 10
PAD = 20
LABEL_H = 26
HEADER_H = 64

BG = (247, 244, 238, 255)
CARD = (255, 255, 255, 255)
CARD_BORDER = (228, 221, 208, 255)
TEXT = (60, 54, 48, 255)
MUTED = (140, 132, 120, 255)
ACCENT = (200, 120, 60, 255)


def _font(size: int):
    from PIL import ImageFont

    for name in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(name).is_file():
            try:
                return ImageFont.truetype(name, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _cell_image(source: Path | bytes) -> Image.Image | None:
    try:
        if isinstance(source, Path):
            im = Image.open(source)
        else:
            im = Image.open(io.BytesIO(source))
        im.seek(0)  # GIF 只取第一帧
        return im.convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def render_grid(
    items: Sequence[Path | bytes],
    *,
    title: str = "表情库",
    subtitle: str = "",
    cols: int = COLS,
    thumb: int = THUMB,
) -> bytes:
    """把若干张图按序号拼成一张 PNG，返回 bytes。"""
    count = len(items)
    cols = max(1, min(cols, count)) if count else cols
    rows = max(1, math.ceil(count / cols))
    width = PAD * 2 + cols * thumb + (cols - 1) * GAP
    height = HEADER_H + PAD + rows * (thumb + LABEL_H + GAP) - GAP + PAD
    canvas = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(canvas)

    title_font = _font(28)
    sub_font = _font(16)
    label_font = _font(18)

    draw.text((PAD, 14), title, font=title_font, fill=TEXT)
    sub = subtitle or f"共 {count} 张"
    draw.text((PAD + 150, 24), sub, font=sub_font, fill=MUTED)
    draw.line(
        [(PAD, HEADER_H - 12), (width - PAD, HEADER_H - 12)],
        fill=CARD_BORDER,
        width=1,
    )

    for idx, item in enumerate(items, 1):
        col = (idx - 1) % cols
        row = (idx - 1) // cols
        x = PAD + col * (thumb + GAP)
        y = HEADER_H + PAD + row * (thumb + LABEL_H + GAP)
        draw.rounded_rectangle(
            (x, y, x + thumb, y + thumb),
            radius=12,
            fill=CARD,
            outline=CARD_BORDER,
            width=1,
        )
        img = _cell_image(item)
        if img is not None:
            img.thumbnail((thumb - 14, thumb - 14), Image.LANCZOS)
            ox = x + (thumb - img.width) // 2
            oy = y + (thumb - img.height) // 2
            canvas.paste(img, (ox, oy), img)

        # 序号角标：居中在卡片下方
        badge = str(idx)
        tw = draw.textlength(badge, font=label_font)
        draw.text(
            (x + (thumb - tw) / 2, y + thumb + 3),
            badge,
            font=label_font,
            fill=ACCENT,
        )

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()

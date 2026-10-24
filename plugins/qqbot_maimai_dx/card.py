"""把「更新成绩」读到的成绩列表渲染成一张图（Pillow，不依赖浏览器/Playwright）。

只画读到的数据：曲目、难度、达成率、DX 分数、FC/FS。
演示模式会额外打一条「演示数据」角标，避免和真实成绩混淆。
"""

import json
import time
from pathlib import Path
from typing import Optional

from nonebot.log import logger

try:
    from PIL import Image, ImageDraw, ImageFont

    AVAILABLE = True
except Exception as exc:  # pragma: no cover - 部署缺依赖时降级为文字
    AVAILABLE = False
    logger.warning(f"成绩图渲染不可用：{type(exc).__name__}: {exc}")
    Image = ImageDraw = ImageFont = None

FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
CATALOG_FILE = Path("/static/data/music_data.json")

DIFF_NAMES = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER")
DIFF_SHORT = {"BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP", "MASTER": "MAS", "Re:MASTER": "ReM"}
BG = (23, 30, 41)
CARD = (32, 42, 56)
TEXT = (232, 238, 246)
MUTED = (150, 164, 182)
ACCENT = (247, 201, 72)

_CATALOG = None


def _catalog() -> dict:
    """曲目表（水鱼 music_data.json）→ {(id, type): item}；读不到就只显示 ID。"""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    _CATALOG = {}
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _CATALOG
    for item in data if isinstance(data, list) else ():
        if not isinstance(item, dict) or "id" not in item:
            continue
        kind = "dx" if str(item.get("type", "")).upper() == "DX" else "standard"
        _CATALOG[(str(item["id"]), kind)] = item
    return _CATALOG


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _cut(text: str, font, limit: int) -> str:
    if font.getbbox(text)[2] <= limit:
        return text
    while text and font.getbbox(text + "…")[2] > limit:
        text = text[:-1]
    return text + "…"


def render(scores: list, *, count: Optional[int] = None, simulated: bool = False) -> Optional[bytes]:
    """成绩列表 → PNG 字节；渲染不可用或数据为空时返回 None。"""
    if not AVAILABLE or not scores:
        return None
    rows = [row for row in scores if isinstance(row, dict)]
    if not rows:
        return None
    rows.sort(key=lambda row: float(row.get("achievements") or 0), reverse=True)
    shown = rows[:12]
    total = count if count is not None else len(rows)

    width, header, row_h, footer = 900, 104, 46, 52
    height = header + row_h * (len(shown) + 1) + footer
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = _font(FONT_BOLD, 30)
    sub_font = _font(FONT_REGULAR, 18)
    cell_font = _font(FONT_REGULAR, 20)
    head_font = _font(FONT_BOLD, 18)
    small_font = _font(FONT_REGULAR, 16)

    draw.rectangle((0, 0, width, 6), fill=ACCENT)
    draw.text((28, 26), "成绩更新", font=title_font, fill=TEXT)
    subtitle = f"共 {total} 条 · {time.strftime('%Y-%m-%d %H:%M')}"
    if simulated:
        subtitle += " · 演示数据（未连接游戏）"
    draw.text((28, 68), subtitle, font=sub_font, fill=MUTED)

    catalogs = _catalog()
    columns = (
        (28, 460, "曲目"),
        (500, 110, "难度"),
        (618, 120, "达成率"),
        (748, 90, "DX"),
        (840, 60, "FC/FS"),
    )
    y = header
    for x, _w, label in columns:
        draw.text((x, y + 8), label, font=head_font, fill=ACCENT)
    y += row_h

    for row in shown:
        song = catalogs.get((str(row.get("id")), str(row.get("type") or "standard"))) or {}
        title = song.get("title") or f"曲目 {row.get('id')}"
        index = row.get("level_index")
        difficulty = DIFF_NAMES[index] if isinstance(index, int) and 0 <= index < 5 else "?"
        level = ""
        if isinstance(index, int) and isinstance(song.get("level"), list) and len(song["level"]) > index:
            level = str(song["level"][index])
        achievement = float(row.get("achievements") or 0)
        marks = []
        for key, labels in (("fc", {"ap": "AP", "fc": "FC"}), ("fs", {"ap": "AP", "fs": "FS"})):
            value = row.get(key)
            if value:
                label = labels.get(str(value).lower(), str(value).upper())
                if label not in marks:
                    marks.append(label)
        marks_text = "/".join(marks) or "—"

        draw.rectangle((16, y + 2, width - 16, y + row_h - 4), fill=CARD)
        draw.text((28, y + 9), _cut(str(title), cell_font, 460), font=cell_font, fill=TEXT)
        diff_text = DIFF_SHORT.get(difficulty, "?") + (f" {level}" if level else "")
        draw.text((500, y + 9), diff_text, font=cell_font, fill=MUTED)
        color = ACCENT if achievement >= 100 else TEXT
        draw.text((618, y + 9), f"{achievement:.4f}%", font=cell_font, fill=color)
        dx = row.get("dx_score")
        draw.text((748, y + 9), str(dx) if dx is not None else "—", font=cell_font, fill=MUTED)
        draw.text((840, y + 9), marks_text, font=cell_font, fill=MUTED)
        y += row_h

    if len(rows) > len(shown):
        draw.text((28, y + 10), f"…还有 {len(rows) - len(shown)} 条未显示", font=small_font, fill=MUTED)
    elif simulated:
        draw.text((28, y + 10), "演示数据：本地模拟，没有访问游戏服务器", font=small_font, fill=MUTED)
    else:
        draw.text((28, y + 10), "数据来源：舞萌官方服读取", font=small_font, fill=MUTED)

    out = Path("/tmp") / "maimai_update.png"
    image.save(out, format="PNG")
    return out.read_bytes()

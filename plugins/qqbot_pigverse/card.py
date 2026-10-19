"""Phase 2：小猪 DX PASS 卡面渲染。

把今日小猪渲染成 DXPASS 风格卡片（1000×620）：
  - 左侧：小猪立绘（按稀有度配色边框 + 光晕）
  - 右侧：玩家名、PIG PASS 标题、稀有度、EX、随机舞萌曲目（曲名/难度/定数）、
    随机城市、达成率、编号
  - 底部：日期 + ERECTHEUS 水印
  - 装饰：假 QR 点阵（带定位角样式）

曲目数据读取 maimaidx 静态资源（MAIMAIDX_PATH/data/music_data.json），
读取失败时回退到内置精选曲目，保证功能可用。
"""

from __future__ import annotations

import json
import os
import random
from datetime import date
from pathlib import Path

from nonebot.log import logger
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .rarity import RARITY_COLOR, RARITY_LABEL

CARD_W, CARD_H = 1000, 620

# 舞萌难度：绿/黄/红/紫（0-3）
DIFFICULTY_NAMES = ("BASIC", "ADVANCED", "EXPERT", "MASTER")
DIFFICULTY_CN = ("绿", "黄", "红", "紫")
DIFFICULTY_COLORS = ("#3fb950", "#d29922", "#f85149", "#a371f7")

CITIES = (
    "广州", "上海", "北京", "深圳", "成都", "武汉", "杭州", "南京",
    "西安", "重庆", "天津", "长沙", "苏州", "郑州", "青岛", "厦门",
)

# 内置精选（maimaidx 静态资源缺失时兜底）
FALLBACK_MUSIC = (
    ("系ぎて", 14.9, "MASTER"),
    ("BATTLE NO.1", 14.7, "MASTER"),
    ("QZKago Requiem", 14.6, "MASTER"),
    ("Grievous Lady", 14.5, "MASTER"),
    ("天火明命", 14.4, "MASTER"),
    ("PANDORA PARADOXXX", 14.2, "MASTER"),
    ("怒槌", 14.1, "MASTER"),
    ("THE EMPEROR", 13.8, "MASTER"),
    ("Inchaos", 13.5, "MASTER"),
    ("Fragrance", 13.0, "EXPERT"),
)


def _maimaidx_path() -> Path:
    return Path(os.getenv("MAIMAIDX_PATH", "/static"))


def _font(size: int, *, cn: bool = False) -> ImageFont.FreeTypeFont:
    """字体选择。

    cn=True 用中文覆盖全的字体（FOT-NewRodin 是日文字体，缺简体字形会变方框）；
    cn=False 用舞萌官方风拉丁字体（标题/编号更好看）。
    """
    base = _maimaidx_path() / "font"
    if cn:
        candidates = [
            base / "ResourceHanRoundedCN-Bold.ttf",
            base / "MapleMono-NF-CN-SemiBold.ttf",
        ]
    else:
        candidates = [
            base / "FOT-NewRodin Pro EB.otf",
            base / "FOT-NewRodin Pro B.otf",
            base / "ResourceHanRoundedCN-Bold.ttf",
        ]
    # rollpig 内置思源黑体（中文全覆盖）兜底
    try:
        import nonebot_plugin_rollpig_plus as rp

        candidates.append(
            Path(rp.__file__).parent
            / "resource"
            / "fonts"
            / "SourceHanSansSC-Medium.otf"
        )
    except Exception:
        pass
    for path in candidates:
        try:
            if path and path.is_file():
                return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return ImageFont.load_default()


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _gradient(w: int, h: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, **kwargs) -> None:
    draw.rounded_rectangle(box, radius=radius, **kwargs)


def _qr_decoration(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, seed: int) -> None:
    """画一个像 QR 的装饰块：随机点阵 + 三个定位角。"""
    rng = random.Random(seed)
    cell = size // 21
    # 点阵
    for row in range(21):
        for col in range(21):
            if rng.random() < 0.42:
                draw.rectangle(
                    [x + col * cell, y + row * cell, x + (col + 1) * cell - 1, y + (row + 1) * cell - 1],
                    fill=(20, 24, 30),
                )
    # 三个定位角（左上/右上/左下）
    def finder(cx: int, cy: int) -> None:
        draw.rectangle([cx, cy, cx + cell * 7, cy + cell * 7], fill=(20, 24, 30))
        draw.rectangle(
            [cx + cell, cy + cell, cx + cell * 6, cy + cell * 6], fill=(255, 255, 255)
        )
        draw.rectangle(
            [cx + cell * 2, cy + cell * 2, cx + cell * 5, cy + cell * 5], fill=(20, 24, 30)
        )

    finder(x, y)
    finder(x + cell * 14, y)
    finder(x, y + cell * 14)


def _fit_image(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """按比例缩放并居中到 box 内（透明背景）。"""
    img = img.convert("RGBA")
    img.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    canvas.paste(img, ((box_w - img.width) // 2, (box_h - img.height) // 2), img)
    return canvas


def _load_music_data() -> list[dict]:
    path = _maimaidx_path() / "data" / "music_data.json"
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
    except Exception as e:
        logger.warning(f"[pigverse] 读取曲目数据失败，回退内置曲库：{e}")
    return []


_MUSIC_CACHE: list[dict] | None = None


def pick_music() -> dict:
    """随机一首曲目 + 随机难度。返回 {title, difficulty, level, ds, type}。"""
    global _MUSIC_CACHE
    if _MUSIC_CACHE is None:
        _MUSIC_CACHE = _load_music_data()
    pool = _MUSIC_CACHE
    if pool:
        song = random.choice(pool)
        ds = song.get("ds") or []
        levels = song.get("level") or []
        idx = random.randrange(len(ds)) if ds else 0
        return {
            "title": str(song.get("title") or "未知曲目"),
            "type": str(song.get("type") or "SD"),
            "difficulty": DIFFICULTY_CN[idx] if idx < len(DIFFICULTY_CN) else "紫",
            "diff_name": DIFFICULTY_NAMES[idx] if idx < len(DIFFICULTY_NAMES) else "MASTER",
            "diff_index": idx,
            "ds": ds[idx] if idx < len(ds) else 0.0,
            "level": levels[idx] if idx < len(levels) else "?",
        }
    title, ds, diff_name = random.choice(FALLBACK_MUSIC)
    return {
        "title": title,
        "type": "DX",
        "difficulty": "紫",
        "diff_name": diff_name,
        "diff_index": 3,
        "ds": ds,
        "level": f"{ds:g}",
    }


def _clip(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    """按像素宽度裁剪文本，超出加省略号。"""
    text = str(text)
    if draw.textlength(text, font=font) <= max_w:
        return text
    out = ""
    for ch in text:
        if draw.textlength(out + ch, font=font) > max_w - 16:
            return out + "…"
        out += ch
    return out


def render_pigpass_card(
    *,
    pig_name: str,
    pig_image_path: Path | None,
    player_name: str,
    ex_level: int,
    rarity: str,
    music: dict,
    city: str,
    day: str | None = None,
    number: int | None = None,
) -> bytes:
    """渲染卡面并返回 PNG 字节。"""
    accent = _hex(RARITY_COLOR.get(rarity, "#9aa4af"))
    rng = random.Random()
    day = day or date.today().isoformat()
    number = number if number is not None else rng.randrange(1, 999999)

    # 背景
    img = _gradient(CARD_W, CARD_H, (10, 14, 21), (26, 32, 44)).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # 背景装饰：对角线光带
    for i in range(-CARD_H, CARD_W, 90):
        draw.line([(i, CARD_H), (i + CARD_H, 0)], fill=(255, 255, 255, 8), width=2)

    # 外框（稀有度色）
    _rounded_rect(draw, [16, 16, CARD_W - 16, CARD_H - 16], 26, outline=accent + (255,), width=3)
    _rounded_rect(draw, [28, 28, CARD_W - 28, CARD_H - 28], 20, outline=(255, 255, 255, 24), width=1)

    # 左侧立绘框
    box = (60, 96, 400, 436)
    draw.rounded_rectangle(
        [box[0] - 10, box[1] - 10, box[2] + 10, box[3] + 10],
        radius=24,
        fill=(246, 247, 250, 255),
        outline=accent + (220,),
        width=3,
    )
    if pig_image_path and Path(pig_image_path).is_file():
        try:
            with Image.open(pig_image_path) as src:
                if getattr(src, "n_frames", 1) > 1:
                    src.seek(0)
                pig_img = _fit_image(src.copy(), box[2] - box[0], box[3] - box[1])
            img.paste(pig_img, (box[0], box[1]), pig_img)
        except Exception as e:
            logger.warning(f"[pigverse] 卡面立绘加载失败：{e}")

    # 稀有度徽章
    badge = f"{rarity} · {RARITY_LABEL.get(rarity, '普通')}"
    badge_font = _font(30, cn=True)
    bw = draw.textlength(badge, font=badge_font)
    draw.rounded_rectangle(
        [box[0] - 10, box[3] + 18, box[0] + bw + 30, box[3] + 74],
        radius=28,
        fill=accent + (230,),
    )
    draw.text((box[0] + 12, box[3] + 27), badge, font=badge_font, fill=(16, 20, 26))

    # 标题
    draw.text((452, 76), "PIG PASS", font=_font(64), fill=(255, 255, 255))
    draw.text((456, 152), "小猪通行证   ERECTHEUS", font=_font(22, cn=True), fill=(140, 150, 165))

    # 玩家名
    draw.text(
        (452, 202),
        _clip(draw, player_name, _font(42, cn=True), 480),
        font=_font(42, cn=True),
        fill=accent,
    )

    # 分隔线
    draw.line([(452, 266), (CARD_W - 80, 266)], fill=(255, 255, 255, 36), width=1)

    # 信息行（可用宽度 560~860，避开右下角 QR）
    info_font = _font(26, cn=True)
    label_font = _font(20, cn=True)
    rows = [
        ("今日小猪", pig_name),
        ("EX 等级", f"Lv.{ex_level}"),
        ("舞萌曲目", music["title"]),
        ("难度定数", f"{music['difficulty']} {music['diff_name']} · {music['ds']:g}"),
        ("出没城市", city),
    ]
    y = 290
    for label, value in rows:
        draw.text((452, y), label, font=label_font, fill=(130, 140, 155))
        draw.text((560, y - 3), _clip(draw, str(value), info_font, 340), font=info_font, fill=(235, 238, 242))
        y += 50

    # QR 装饰（右下角，避开信息区与底线）
    _qr_decoration(draw, CARD_W - 136, 56, 92, seed=rng.randrange(1 << 30))

    # 底部：日期左、编号右对齐
    draw.line([(452, 548), (CARD_W - 80, 548)], fill=(255, 255, 255, 30), width=1)
    draw.text((452, 562), day, font=_font(22), fill=(130, 140, 155))
    num_font = _font(22)
    num_text = f"NO.{number:06d}"
    num_w = draw.textlength(num_text, font=num_font)
    draw.text((CARD_W - 80 - num_w, 562), num_text, font=num_font, fill=(130, 140, 155))

    from io import BytesIO

    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()

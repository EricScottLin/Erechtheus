import os, asyncio
from random import random
from PIL import Image, ImageDraw, ImageFont
from math import ceil

from ... import models, constants, api_handler, util


from .. import _paths

# 素材不在仓库内，路径必须每次出图时解析（可能在 import 之后才下载完）。

def __generate_single_song_sync(idx: int, card_type: str, song: models.DMSong, cover: Image.Image) -> Image.Image:
    with Image.open(os.path.join(_paths.bests_assets(), f"{card_type}_card.png")) as overlay:
        bg = Image.new("RGBA", overlay.size)
        with cover.resize((160, 160)) as _cover:
            bg.paste(_cover)
        bg.alpha_composite(overlay)

    draw = ImageDraw.Draw(bg)
    font_bd = ImageFont.truetype(os.path.join(_paths.fonts(), "Respect_bd.ttf"), 23)
    font_rg = ImageFont.truetype(os.path.join(_paths.fonts(), "Respect_rg.ttf"), 30)
    draw.text((10, 141), song.dlc_code, font=font_bd, fill=constants.DLC_COLOR.get(song.dlc_code, 'white'), anchor="lm")
    draw.text((165, 21), util.wrap_text(song.title, font_rg, 300), font=font_rg, fill='white', anchor="lm")

    font_bd = font_bd.font_variant(size=30)
    draw.text((230, 95), f"{song.score:.2f}%", font=font_bd, fill='white', anchor="lm")
    draw.text((292, 135), f"{song.djpower:.4f}", font=font_bd, fill='#F0BE40', anchor="lm")

    font_bd = font_bd.font_variant(size=26)
    draw.text((516, 17), f"#{idx}", font=font_bd, fill="black" if card_type == "new" else "#333333", anchor="mm")

    is_sc = song.pattern == "SC"
    with util.assemble_diff_strip_level(is_sc=is_sc, level_to=song.level, diff_star_path=os.path.join(_paths.bests_assets(), "diff_stars")) as diff_strip:
        bg.alpha_composite(diff_strip, (165, 52))

    if mc_state := util.get_mc_state(song.score, song.max_combo):
        with Image.open(os.path.join(_paths.bests_assets(), f"{mc_state}.png")) as mc_overlay:
            bg.alpha_composite(mc_overlay)

    return bg

async def generate_single_song(idx: int, card_type: str, song: models.DMSong) -> Image.Image:
    with await api_handler.get_cover(song.songid) as cover:
        img = await asyncio.to_thread(__generate_single_song_sync, idx, card_type, song, cover)
    return img


async def assemble_song_cards(songs: list[models.DMSong], card_type: str) -> Image.Image | None:
    gap = 20
    layout_width = 5
    bg = None

    for idx, song in enumerate(songs):
        with await generate_single_song(idx + 1, card_type, song) as song_image:
            if bg is None:
                bg = Image.new(
                    "RGBA",
                    (
                        (song_image.width + gap) * layout_width,
                        (song_image.height + gap) * ceil(len(songs) / layout_width)
                    )
                )

            x = (idx % 5) * (song_image.width + gap)
            y = (idx // 5) * (song_image.height + gap)
            bg.paste(song_image, (x, y))

    return bg


def assemble_background(data: models.DMBests, is_max: bool) -> Image.Image:
    bmode_strip_box = (119, 40, 151, 468)
    emblem_lt = (1029, 129)

    bg = Image.open(os.path.join(_paths.bests_assets(), "bg.png"))
    draw = ImageDraw.Draw(bg)

    djpower_tier, djpower_level = util.get_djpower_tier(data.total_djpower)
    djpower_desc = util.format_djpower_tier(djpower_tier, djpower_level)
    djpower_color = constants.DJPOWER_TIER_COLOR[djpower_tier]

    if random() < 0.1 and os.path.exists(os.path.join(os.path.join(_paths.bests_assets(), "emblems"), f"{djpower_tier}_0.png")): # Easter egg
        djpower_level = 0

    if os.path.exists(os.path.join(os.path.join(_paths.bests_assets(), "emblem_bg"), f"{data.username}.png")):
        with Image.open(os.path.join(os.path.join(_paths.bests_assets(), "emblem_bg"), f"{data.username}.png")) as emblem_bg:
            bg.alpha_composite(emblem_bg, emblem_lt)

    if not is_max:
        with Image.open(os.path.join(os.path.join(_paths.bests_assets(), "emblems"), f"{djpower_tier}_{djpower_level}.png")) as emblem:
            bg.alpha_composite(emblem, emblem_lt)

    font_bd = ImageFont.truetype(os.path.join(_paths.fonts(), "Respect_bd.ttf"), 320)
    font_rg = ImageFont.truetype(os.path.join(_paths.fonts(), "Respect_rg.ttf"), 80)
    font_lt = ImageFont.truetype(os.path.join(_paths.fonts(), "Respect_lt.ttf"), 58)

    draw.rectangle(bmode_strip_box, fill=constants.BMODE_COLOR[data.bmode])
    draw.text((282, 370), str(data.bmode), font=font_bd, fill='white', anchor="ms")
    draw.text((1535, 187), data.username, font=font_rg, fill='white', anchor="lm")
    font_bd = font_bd.font_variant(size=64)
    draw.text((1535, 281), djpower_desc, font=font_bd, fill=djpower_color, anchor="lm")
    font_rg = font_rg.font_variant(size=120)
    if is_max:
        draw.text((1535, 375), f"10000.0000", font=font_rg, fill='white', anchor="lm")
    else:
        draw.text((1535, 375), f"{data.total_djpower:.4f}", font=font_rg, fill='white', anchor="lm")
    font_rg = font_rg.font_variant(size=42)
    draw.text((1795, 464), f"{data.total_djpower_raw:.4f}", font=font_rg, fill='white', anchor="lm")

    font_rg = font_rg.font_variant(size=76)
    font_rg_s = font_rg.font_variant(size=46)

    draw.text((2520, 580), f"{data.total_basic_djpower:.4f}", font=font_rg, fill='white', anchor="mm")
    draw.text((2850, 600), f"{data.total_basic_djpower_raw:.4f}", font=font_rg_s, fill='white', anchor="mm")

    draw.text((2520, 3295), f"{data.total_new_djpower:.4f}", font=font_rg, fill='white', anchor="mm")
    draw.text((2850, 3315), f"{data.total_new_djpower_raw:.4f}", font=font_rg_s, fill='white', anchor="mm")

    conv_const, maxpower = api_handler.get_convert_constant(data.bmode)
    draw.text((570, 4553), f"{conv_const:.8f} ({maxpower:.2f})", font=font_lt, fill='white', anchor="ls")

    return bg

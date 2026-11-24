from PIL import Image

from . import api_handler, models, generators


async def _ensure_assets() -> None:
    """出图前确保素材就绪。

    素材不随仓库分发，首次出图时才下载；下载失败要给出可操作的提示，
    而不是让调用方拿到一个 FileNotFoundError。
    """
    try:
        from ..assets import ensure_assets

        await ensure_assets()
    except ImportError:
        # 独立使用 djmax_bests（不经过 qqbot_djmax）时没有 assets 模块，跳过。
        pass


async def generate_bests(username: str, bmode: int) -> Image.Image:
    await _ensure_assets()
    bests_data = await api_handler.fetch_bests(username, bmode)
    return await generators.generate_bests_image(bests_data)


async def generate_bests_theoretical(bmode: int) -> Image.Image:
    await _ensure_assets()
    song_db = await api_handler.fetch_song_db()
    bests_data = models.DMBests.get_theoretical_bests(bmode, song_db)
    return await generators.generate_bests_image(bests_data, is_max=True)


async def generate_scorelist(username: str, bmode: int, is_sc: bool, level: int) -> Image.Image:
    await _ensure_assets()
    scorelist_data = await api_handler.fetch_scorelist(username, bmode, is_sc, level)
    return await generators.generate_scorelist_by_level(scorelist_data)


async def generate_scorelist_new(username: str, bmode: int, diff: str) -> Image.Image:
    await _ensure_assets()
    scorelist_data = await api_handler.fetch_scorelist_new(username, bmode, diff)
    return await generators.generate_scorelist_by_pack(scorelist_data, "NEW")


async def generate_scorelist_pack(username: str, bmode: int, diff: str, pack: str) -> Image.Image:
    await _ensure_assets()
    scorelist_data = await api_handler.fetch_scorelist_pack(username, bmode, diff, pack)
    return await generators.generate_scorelist_by_pack(scorelist_data, pack)


async def generate_scorelist_pp(username: str, bmode: int) -> Image.Image:
    await _ensure_assets()
    scorelist_data = await api_handler.fetch_scorelist_pp(username, bmode)
    return await generators.generate_scorelist_pp(scorelist_data)

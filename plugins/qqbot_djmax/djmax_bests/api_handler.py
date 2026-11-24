import httpx, os, urllib.parse
from PIL import Image
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import models


def _writable_dir(name: str) -> str:
    """缓存目录：优先环境变量，其次插件数据目录，最后退回包内目录。

    线上插件目录是只读挂载，写包内目录会直接 OSError，所以必须优先用可写位置。
    """
    env = os.environ.get(f"DJMAX_{name.upper()}_DIR")
    if env:
        return env
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        return os.path.join(str(get_plugin_data_dir()), name)
    except Exception:
        pass
    local = os.path.join(os.path.dirname(__file__), name)
    if os.access(os.path.dirname(local), os.W_OK):
        return local
    import tempfile

    return os.path.join(tempfile.gettempdir(), "qqbot_djmax", name)


COVER_PATH = _writable_dir('covers')
CACHE_PATH = _writable_dir('cache')
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "sh-util-bot/gh:SoreHait/djmax_bests_generate"
}

client = httpx.AsyncClient(headers=HEADERS)
RETRIES = 3


if not os.path.exists(COVER_PATH):
    os.makedirs(COVER_PATH)

if not os.path.exists(CACHE_PATH):
    os.makedirs(CACHE_PATH)


async def get_with_retry_async(url) -> httpx.Response:
    global client, RETRIES

    for _ in range(RETRIES):
        try:
            return await client.get(url)
        except httpx.TimeoutException:
            continue
    raise httpx.TimeoutException(f"Failed to fetch {url} after {RETRIES} retries")

def get_with_retry(url) -> httpx.Response:
    global RETRIES, HEADERS

    for _ in range(RETRIES):
        try:
            return httpx.get(url, headers=HEADERS)
        except httpx.TimeoutException:
            continue
    raise httpx.TimeoutException(f"Failed to fetch {url} after {RETRIES} retries")


async def get_cover(songid: int) -> Image.Image:
    img_path = os.path.join(COVER_PATH, f'{songid}.jpg')
    if os.path.exists(img_path):
        return Image.open(img_path)

    url = f"https://v-archive.net/s3/images/jackets/{songid}.jpg"
    print(f'Fetching cover {songid}')
    response = await get_with_retry_async(url)
    if response.status_code == 200:
        with open(img_path, 'wb') as f:
            f.write(response.content)
        return Image.open(img_path)
    else:
        print(f'Failed to get cover {songid}')
        return Image.new('RGB', (80, 80), color='black')

def get_convert_constant(bmode: int) -> tuple[Decimal, Decimal]:
    maxpower_file = os.path.join(CACHE_PATH, f'maxpower_{bmode}.txt')
    if os.path.exists(maxpower_file):
        with open(maxpower_file, 'r') as f:
            maxpower = Decimal(f.read().strip())
            return Decimal("10000.0000") / maxpower, maxpower

    print(f'maxpower cache for {bmode}b invalidated, fetching new one')
    url = f"https://v-archive.net/api/v2/archive/DEV/djClass/{bmode}"
    response = get_with_retry(url)
    response.raise_for_status()
    data = response.json()
    with open(maxpower_file, 'w') as f:
        f.write(str(data["maxDjPower"]))
    maxpower = Decimal(str(data["maxDjPower"]))
    return Decimal("10000.0000") / maxpower, maxpower

async def fetch_song_db() -> "models.DMSongDB":
    from .models import DMSongDB
    db_path = os.path.join(CACHE_PATH, 'songs.json')
    if os.path.exists(db_path):
        with open(db_path, 'r', encoding='utf-8') as f:
            return DMSongDB.model_validate_json(f.read())

    print("songdb cache invalidated, fetching new one")
    url = "https://v-archive.net/db/v2/songs.json"
    response = await get_with_retry_async(url)
    response.raise_for_status()
    with open(db_path, 'w', encoding='utf-8') as f:
        f.write(response.text)
    return DMSongDB.model_validate_json(response.text)

async def fetch_dlc_list() -> "models.VADLCList":
    from .models import VADLCList
    dlc_path = os.path.join(CACHE_PATH, 'dlcs.json')
    if os.path.exists(dlc_path):
        with open(dlc_path, 'r', encoding='utf-8') as f:
            return VADLCList.model_validate_json(f.read())

    print("dlc list cache invalidated, fetching new one")
    url = "https://v-archive.net/db/dlcs.json"
    response = await get_with_retry_async(url)
    response.raise_for_status()
    with open(dlc_path, 'w', encoding='utf-8') as f:
        f.write(response.text)
    return VADLCList.model_validate_json(response.text)

def remove_cache():
    cache_files = os.listdir(CACHE_PATH)
    for file in cache_files:
        file_path = os.path.join(CACHE_PATH, file)
        if os.path.isfile(file_path):
            os.remove(file_path)
    print("Cache cleared")


def build_req_url(username: str, bmode: int, **kwargs) -> str:
    query = urllib.parse.urlencode(kwargs)
    return f"https://v-archive.net/api/v2/archive/{username}/button/{bmode}?{query}"

async def fetch_bests(username: str, bmode: int) -> "models.DMBests":
    from .models import DMBests, VAResponse
    print(f'Fetching bests for {username} - {bmode}b')
    url_basic = build_req_url(
        username,
        bmode,
        newTab = "false",
        sort = "djpower",
        order = "desc",
        limit = "70"
    )
    url_new = build_req_url(
        username,
        bmode,
        newTab = "true",
        sort = "djpower",
        order = "desc",
        limit = "30"
    )
    response_basic = await get_with_retry_async(url_basic)
    response_basic.raise_for_status()
    response_new = await get_with_retry_async(url_new)
    response_new.raise_for_status()

    va_resp_basic = VAResponse.model_validate_json(response_basic.text)
    va_resp_new = VAResponse.model_validate_json(response_new.text)
    return DMBests.from_va_response(va_resp_basic, va_resp_new)

async def fetch_scorelist(username: str, bmode: int, is_sc: bool, level: int) -> "models.DMScorelist":
    from .models import DMScorelist, VAResponse
    print(f'Fetching scorelist for {username} - {bmode}b - {"SC" if is_sc else "^SC"} - level {level}')
    url = build_req_url(
        username,
        bmode,
        pattern = "SC" if is_sc else "NM,HD,MX",
        levelMin = level,
        levelMax = level
    )
    response = await get_with_retry_async(url)
    response.raise_for_status()
    song_db = await fetch_song_db()
    va_resp = VAResponse.model_validate_json(response.text)
    return DMScorelist.from_va_response_level(is_sc, level, song_db, va_resp)

async def fetch_scorelist_new(username: str, bmode: int, diff: str) -> "models.DMScorelist":
    from .models import DMScorelist, VAResponse
    print(f'Fetching scorelist for {username} - {bmode}b - NEW - {diff}')
    url = build_req_url(
        username,
        bmode,
        pattern = diff,
        newTab = "true"
    )
    response = await get_with_retry_async(url)
    response.raise_for_status()
    song_db = await fetch_song_db()
    va_resp = VAResponse.model_validate_json(response.text)
    return DMScorelist.from_va_response_new(diff, song_db, va_resp)

async def fetch_scorelist_pack(username: str, bmode: int, diff: str, pack: str) -> "models.DMScorelist":
    from .models import DMScorelist, VAResponse
    print(f'Fetching scorelist for {username} - {bmode}b - {pack} - {diff}')
    url = build_req_url(
        username,
        bmode,
        pattern = diff,
        dlc = pack
    )
    response = await get_with_retry_async(url)
    response.raise_for_status()
    song_db = await fetch_song_db()
    va_resp = VAResponse.model_validate_json(response.text)
    return DMScorelist.from_va_response_pack(diff, pack, song_db, va_resp)

async def fetch_scorelist_pp(username: str, bmode: int) -> "models.DMScorelist":
    from .models import DMScorelist, VAResponse
    print(f'Fetching PP scorelist for {username} - {bmode}b')
    url = build_req_url(
        username,
        bmode,
        sort = "score",
        order = "desc"
    )
    response = await get_with_retry_async(url)
    response.raise_for_status()
    va_resp = VAResponse.model_validate_json(response.text)
    return DMScorelist.from_va_response_pp(va_resp)


# --- 单曲成绩查询 -------------------------------------------------------------
# archive 接口一次返回某玩家在某键数下的全部成绩，单曲查询需要按 songid 取，
# 这里用短 TTL 内存缓存，避免同一个人连续查不同歌时反复拉全量数据。

_RECORD_TTL = 180  # 秒
_record_cache: dict[tuple[str, int], tuple[float, dict[tuple[int, int, str], "SingleRecord"]]] = {}


class SingleRecord(BaseModel):
    """archive 接口返回的单个谱面成绩。"""

    title: int
    pattern: Literal["NM", "HD", "MX", "SC"]
    level: int
    score: Decimal
    max_combo: bool = Field(alias="maxCombo")
    djpower: Decimal
    floor_name: Decimal | None = Field(default=None, alias="floorName")
    rating: Decimal | None = None
    max_rating: int | None = Field(default=None, alias="maxRating")
    updated_at: str | None = Field(default=None, alias="updatedAt")


async def fetch_song_records(
    username: str, bmodes: tuple[int, ...] | list[int]
) -> dict[tuple[int, int, str], "SingleRecord"]:
    """按 (songid, bmode, pattern) 索引该玩家的全部成绩。"""
    import time as _time

    result: dict[tuple[int, int, str], SingleRecord] = {}
    for bmode in bmodes:
        key = (username, bmode)
        cached = _record_cache.get(key)
        if cached is not None and _time.monotonic() - cached[0] < _RECORD_TTL:
            result.update(cached[1])
            continue

        # 不带 limit 时服务端返回该键数下全部成绩（带 limit=0 会被拒绝为 400）。
        url = build_req_url(username, bmode, sort="djpower", order="desc")
        response = await get_with_retry_async(url)
        response.raise_for_status()
        payload = response.json()
        table: dict[tuple[int, int, str], SingleRecord] = {}
        for raw in payload.get("records") or []:
            try:
                record = SingleRecord.model_validate(raw)
            except ValidationError:
                continue
            table[(record.title, bmode, record.pattern)] = record
        _record_cache[key] = (_time.monotonic(), table)
        result.update(table)
    return result


def clear_record_cache() -> None:
    _record_cache.clear()


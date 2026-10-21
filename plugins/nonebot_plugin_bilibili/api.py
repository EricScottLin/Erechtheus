"""B站数据接口封装。

踩坑记录（2026-09 实测，改动前务必看）：
1. **B站现已强制风控**：裸请求（不带 UA/Referer）一律 HTTP 412，返回反爬 HTML 而非 JSON。
   所有请求必须带 User-Agent + Referer: https://www.bilibili.com/。
   （很多 2023~2024 年停更的开源 B站插件就是因为没处理这点而失效。）
2. `x/space/upstat`（UP主总播放量）已废：加不加 wbi 都返回 code=0 但字段全是 None。
   APP 端接口要签名、web 端 `arc/search` 返回 -352 风控 → **总播放量目前无法可靠获取**，
   只能优雅降级。
3. 按昵称搜 UP 主（`x/web-interface/wbi/search/type`）必须走 **wbi 签名**，否则 412。
4. wbi 的 mixin_key 每天变，需按天重新取；本地缓存即可。
"""
import asyncio
import hashlib
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import httpx
from nonebot.log import logger

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

# 可选：B站账号 Cookie。带上 SESSDATA 后 playurl 才会返回 720P/1080P
# （匿名只有 360P/480P）。不配也能正常解析，只是最高 480P。
SESSDATA = os.getenv("BILI_SESSDATA", "").strip()


def _apply_cookie(buvid3: str = "", buvid4: str = "") -> None:
    """把 SESSDATA + buvid 拼进请求头。

    buvid3/buvid4 是 B站给「匿名访客」发的设备指纹，别看不起眼：
    机房 IP 不带它，`x/web-interface/wbi/view` 连续请求必然 412（实测 6/6 挂）；
    带上之后同样的连发 6/6 全 200。这是本插件最关键的防风控手段。
    """
    parts = []
    if SESSDATA:
        parts.append(f"SESSDATA={SESSDATA}")
    if buvid3:
        parts.append(f"buvid3={buvid3}")
    if buvid4:
        parts.append(f"buvid4={buvid4}")
    if parts:
        HEADERS["Cookie"] = "; ".join(parts)

API_VIDEO = "https://api.bilibili.com/x/web-interface/view"
API_WBI_VIDEO = "https://api.bilibili.com/x/web-interface/wbi/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"
API_CARD = "https://api.bilibili.com/x/web-interface/card"
API_RELATION = "https://api.bilibili.com/x/relation/stat"
API_RANKING = "https://api.bilibili.com/x/web-interface/ranking/v2"
API_POPULAR = "https://api.bilibili.com/x/web-interface/popular"
API_SEARCH_USER = "https://api.bilibili.com/x/web-interface/wbi/search/type"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_FINGER = "https://api.bilibili.com/x/frontend/finger/spi"

# buvid 缓存文件（不用每次启动都去要一次，也不怕重启后头几分钟又被 412）
_BUVID_FILE = Path(os.getenv("BILI_BUVID_FILE", "data/bili_buvid.json"))
_buvid_ready = False

TIMEOUT = 15.0

# wbi 签名用的固定置换表
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_mixin_key: str = ""
_mixin_key_day: int = 0

# BV 号 / av 号 / b23 短链
_RE_BV = re.compile(r"(BV[0-9A-Za-z]{10})", re.I)
_RE_AV = re.compile(r"(?:av|AV)(\d{1,12})")
_RE_B23 = re.compile(r"(?:https?://)?(?:www\.)?(?:b23\.tv|bili2233\.cn)/[0-9A-Za-z]+", re.I)
_RE_SHORTurl = re.compile(
    r"(?:https?://)?(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)", re.I)


class BiliError(Exception):
    """B站接口返回错误。"""


# 风控码：-352 请求过密 / -509 请求过于频繁 / -412 反爬
RISK_CODES = {-352, -509, -412}
RISK_TIP = "B站临时风控（请求太频繁），歇 30 秒再试~"
_RISK_RETRY = 2                 # 风控重试次数（首次 + 2 次重试）
_RISK_BACKOFF = (2.0, 5.0)      # 每次重试前等多久；机房 IP 的 412 几秒内多半能解


class BiliRiskError(BiliError):
    """撞上 B站风控（可重试 / 可降级）。"""

    def __init__(self, code: int = -352):
        super().__init__(RISK_TIP)
        self.code = code


class NotVideoLink(BiliError):
    """短链解析出来不是视频（可能是直播/专栏/动态），群里应静默忽略。"""

    def __init__(self):
        super().__init__("这不是视频链接（可能是直播/专栏/动态）")


def _today() -> int:
    return int(time.time()) // 86400


async def _ensure_buvid(client: httpx.AsyncClient, refresh: bool = False) -> None:
    """确保请求头里有 buvid3/buvid4（设备指纹），没有就去要一对。

    只做一次（失败也不阻塞主流程）；收到 412 时用 refresh=True 强制换一对再试。
    优先读本地缓存，避免每次重启都多打一次 B站。
    """
    global _buvid_ready
    if _buvid_ready and not refresh:
        return
    _buvid_ready = True
    b3 = b4 = ""
    if not refresh:
        try:
            cached = json.loads(_BUVID_FILE.read_text(encoding="utf-8"))
            b3, b4 = cached.get("b_3", ""), cached.get("b_4", "")
        except Exception:
            pass
    if not b3:
        try:
            r = await client.get(API_FINGER, headers=HEADERS, timeout=TIMEOUT)
            data = (r.json() or {}).get("data") or {}
            b3, b4 = data.get("b_3", ""), data.get("b_4", "")
            if b3:
                try:
                    _BUVID_FILE.parent.mkdir(parents=True, exist_ok=True)
                    _BUVID_FILE.write_text(
                        json.dumps({"b_3": b3, "b_4": b4}), encoding="utf-8")
                except Exception as e:
                    logger.debug(f"[bilibili] 缓存 buvid 失败: {e}")
        except Exception as e:
            logger.warning(f"[bilibili] 获取 buvid 失败（解析仍可继续）: {e}")
    _apply_cookie(b3, b4)


async def _get_mixin_key(client: httpx.AsyncClient) -> str:
    """取 wbi mixin_key，按天缓存。"""
    global _mixin_key, _mixin_key_day
    if _mixin_key and _mixin_key_day == _today():
        return _mixin_key
    await _ensure_buvid(client)
    r = await client.get(API_NAV, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    wbi = r.json()["data"]["wbi_img"]
    img = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    raw = img + sub
    _mixin_key = "".join(raw[i] for i in _MIXIN_TAB)[:32]
    _mixin_key_day = _today()
    return _mixin_key


def _wbi_sign(params: dict, key: str) -> dict:
    params = dict(params)
    params["wts"] = int(time.time())
    # 过滤 !'()* 五个字符后再字典序拼接
    clean = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
             for k, v in params.items()}
    query = urllib.parse.urlencode(sorted(clean.items()))
    params["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return params


async def _wbi_get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """带 wbi 签名的 GET。

    `x/web-interface/view` 从机房 IP 直连会被 412（2026-10 实测，本机同 IP 上
    nav/popular/card 都 200，唯独 view 412），但走 wbi 签名的
    `x/web-interface/wbi/view` 正常返回 —— 这是 B站 web 播放器自己用的入口。
    再配上 buvid 设备指纹后连发也不会 412（见 _ensure_buvid）。
    """
    key = await _get_mixin_key(client)
    return await _jget(client, url, _wbi_sign(params, key))


async def _jget(client: httpx.AsyncClient, url: str, params: dict,
                _try: int = 0) -> dict:
    """GET 并校验 B站业务码。

    对风控码（-352/-509/412）做退避重试。注意 B站机房 IP 的 412 一旦触发，
    通常要几秒到几十秒才解除，所以退避是 2s / 5s / 10s；第一次撞上时先换一对
    buvid 设备指纹再试（换掉后基本立刻恢复）。
    """
    await _ensure_buvid(client)
    try:
        r = await client.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        raise BiliError(f"网络请求失败：{type(e).__name__}")

    if r.status_code == 412:
        if _try < _RISK_RETRY:
            if _try == 0:
                await _ensure_buvid(client, refresh=True)
            await asyncio.sleep(_RISK_BACKOFF[min(_try, len(_RISK_BACKOFF) - 1)])
            return await _jget(client, url, params, _try + 1)
        raise BiliRiskError(-412)

    try:
        j = r.json()
    except Exception:
        raise BiliError("接口返回了非 JSON（疑似风控页），请稍后再试")

    code = j.get("code")
    if code != 0:
        if code in RISK_CODES:
            if _try < _RISK_RETRY:
                if _try == 0:
                    await _ensure_buvid(client, refresh=True)
                await asyncio.sleep(_RISK_BACKOFF[min(_try, len(_RISK_BACKOFF) - 1)])
                return await _jget(client, url, params, _try + 1)
            raise BiliRiskError(code)
        raise BiliError(f"B站返回错误 code={code}：{j.get('message') or '未知错误'}")
    return j.get("data") or {}


def extract_target(text: str):
    """从一段文本里提取视频标识。

    返回 ("bvid", xxx) / ("aid", xxx) / ("url", b23短链)，没有则返回 None。
    """
    if not text:
        return None
    m = _RE_BV.search(text)
    if m:
        return ("bvid", m.group(1))
    m = _RE_SHORTurl.search(text)
    if m:
        v = m.group(1)
        return ("aid", v[2:]) if v.lower().startswith("av") else ("bvid", v)
    m = _RE_AV.search(text)
    if m:
        return ("aid", m.group(1))
    m = _RE_B23.search(text)
    if m:
        u = m.group(0)
        return ("url", u if u.startswith("http") else "https://" + u)
    return None


async def resolve_short_url(client: httpx.AsyncClient, url: str) -> str:
    """把 b23.tv 短链还原成含 BV 号的完整地址。

    注意：只读 302 的 Location 头，绝不能跟随进入 www.bilibili.com 页面 ——
    那里对无 Cookie 请求一律 412 反爬（2026-09 服务器实测）。
    """
    cur = url
    try:
        for _ in range(5):
            r = await client.get(cur, headers=HEADERS, timeout=TIMEOUT,
                                 follow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    return ""
                cur = str(httpx.URL(r.url).join(loc))
                if _RE_BV.search(cur):
                    return cur
                continue
            # 到达最终页（无论 200 还是 412），URL 就是我们手里这个
            cur = str(r.url)
            break
    except Exception as e:
        logger.warning(f"[bilibili] 短链还原失败: {e}")
        return ""
    # 短链可能指向 av 号视频（如 b23.tv/av170001 -> /video/av170001）
    return cur if (_RE_BV.search(cur) or _RE_SHORTurl.search(cur)) else ""


async def fetch_video(key: str, value: str) -> dict:
    """视频详情：标题/简介/UP/播放/点赞/投币/收藏/分享/评论。

    注意：走 wbi 签名的 wbi/view。裸 view 接口从机房 IP 请求会 412（反爬），
    见 _wbi_get 的说明。
    """
    async with httpx.AsyncClient() as client:
        if key == "url":
            resolved = await resolve_short_url(client, value)
            t = extract_target(resolved) if resolved else None
            if not t:
                raise NotVideoLink()
            key, value = t
        data = await _wbi_get(client, API_WBI_VIDEO, {key: value})
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        pages = data.get("pages") or []
        return {
            "title": data.get("title", ""),
            "bvid": data.get("bvid", ""),
            "desc": (data.get("desc") or "").strip(),
            "pic": data.get("pic", ""),
            "pubdate": data.get("pubdate", 0),
            "duration": data.get("duration", 0),
            "cid": data.get("cid") or (pages[0].get("cid") if pages else 0),
            "pages": len(pages),
            "owner_name": owner.get("name", ""),
            "owner_mid": owner.get("mid", 0),
            "view": stat.get("view", 0),
            "danmaku": stat.get("danmaku", 0),
            "reply": stat.get("reply", 0),
            "favorite": stat.get("favorite", 0),
            "coin": stat.get("coin", 0),
            "share": stat.get("share", 0),
            "like": stat.get("like", 0),
        }


async def fetch_playurl(bvid: str, cid: int, qn: int = 64) -> dict:
    """取播放地址（DASH 优先，含分离的音视频流）。

    fnval=16 表示请求 DASH；匿名可拿到 480P/720P（1080P 及以上需登录）。
    返回值直接用 pick_streams() 挑选。
    """
    async with httpx.AsyncClient() as client:
        return await _jget(
            client, API_PLAYURL,
            {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 16, "fourk": 0},
        )


def _codec_rank(codecs: str) -> int:
    """编码优先级：avc1(h264) > hev1/hvc1(hevc) > 其他。压缩重编码时 h264 解码最快。"""
    c = (codecs or "").lower()
    if c.startswith("avc1"):
        return 0
    if c.startswith(("hev1", "hvc1")):
        return 1
    return 2


def _short_side(v: dict) -> int:
    """画质档位看「短边」：横屏是 height，竖屏（如 480x852）是 width。"""
    w = int(v.get("width") or 0)
    h = int(v.get("height") or 0)
    if w and h:
        return min(w, h)
    return h or w or 0


def pick_streams(play: dict, max_height: int = 480):
    """从 playurl 结果里挑视频流 + 音频流。

    返回 (video: dict, audio: dict|None, duration: int)。
    没有 DASH（老视频只有 durl 渐进流）时返回 (None, None, duration)，
    调用方用 durl 直接下载。
    """
    dash = play.get("dash") or {}
    duration = int(dash.get("duration") or play.get("timelength", 0) // 1000)
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    if not videos:
        return None, None, duration

    # 优先 avc1，其次 hevc；档位 ≤max_height 里挑最高的，避免超清浪费带宽
    cand = [v for v in videos if _short_side(v) <= max_height] or videos
    cand.sort(key=lambda v: (
        0 if _short_side(v) <= max_height else 1,
        -_short_side(v),
        _codec_rank(v.get("codecs", "")),
        -int(v.get("bandwidth") or 0),
    ))
    video = cand[0]

    audio = None
    if audios:
        audio = max(audios, key=lambda a: int(a.get("bandwidth") or 0))
    return video, audio, duration


async def download_file(url: str, path: str, timeout: float = 180.0,
                        retries: int = 3) -> int:
    """下载一个流到本地文件（CDN 必须带 UA + Referer）。

    B站 CDN 节点限流/掉线很常见（RemoteProtocolError / 403 / 509），所以：
    - 失败退避重试（1.5s、3s、4.5s）；
    - 每次都从已下载的字节数用 `Range` 续传，而不是从头再来；
    - 服务器不支持 Range（返回 200）时自动改为重头写。
    返回字节数，最终失败抛 BiliError（带最后一次的错误类型）。大文件流式写。
    """
    last = "未知错误"
    target = Path(path)
    for attempt in range(retries + 1):
        done = target.stat().st_size if target.exists() else 0
        headers = dict(HEADERS)
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                async with client.stream("GET", url, headers=headers,
                                         timeout=httpx.Timeout(timeout)) as r:
                    if done and r.status_code == 416:   # 已经下完了
                        return done
                    r.raise_for_status()
                    if r.status_code != 206:            # 不支持续传，重头写
                        done = 0
                    mode = "ab" if done else "wb"
                    with open(target, mode) as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                            done += len(chunk)
            return done
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 416:
                return target.stat().st_size
            last = f"HTTP {e.response.status_code}"
        except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectError):
            last = "连接被CDN断开"
        except Exception as e:
            last = type(e).__name__
        if attempt < retries:
            await asyncio.sleep(1.5 * (attempt + 1))
    raise BiliError(f"下载视频流失败：{last}")


async def fetch_up_card(mid: int) -> dict:
    """UP主数据：昵称/粉丝/关注/稿件数/获赞数。播放量 B站已废，返回 None。"""
    async with httpx.AsyncClient() as client:
        card = await _jget(client, API_CARD, {"mid": mid})
        rel = {}
        try:
            rel = await _jget(client, API_RELATION, {"vmid": mid})
        except BiliError as e:
            logger.warning(f"[bilibili] relation/stat 失败（不影响主数据）: {e}")
        c = card.get("card") or {}
        return {
            "name": c.get("name", ""),
            "mid": c.get("mid", mid),
            "sign": (c.get("sign") or "").strip(),
            "fans": card.get("follower", 0),
            "archive_count": card.get("archive_count", 0),
            "like_num": card.get("like_num", 0),
            "attention": card.get("attention"),  # card 里常为 None，改用 rel
            "following": rel.get("following"),
            "follower": rel.get("follower"),
            "space_url": f"https://space.bilibili.com/{mid}",
        }


async def search_up(keyword: str, limit: int = 5) -> list:
    """按昵称关键字搜 UP 主（需 wbi 签名）。"""
    async with httpx.AsyncClient() as client:
        try:
            key = await _get_mixin_key(client)
            params = _wbi_sign(
                {"search_type": "bili_user", "keyword": keyword}, key)
            data = await _jget(client, API_SEARCH_USER, params)
        except BiliError:
            raise
        except Exception as e:
            raise BiliError(f"搜索失败：{type(e).__name__}")
        out = []
        for it in (data.get("result") or [])[:limit]:
            out.append({
                "name": re.sub(r"<[^>]+>", "", it.get("uname") or ""),
                "mid": it.get("mid", 0),
                "fans": it.get("fans", 0),
                "videos": it.get("videos", 0),
            })
        return out


# 分区 rid 对照（全站 0）
SECTIONS = {
    "全站": 0, "动画": 1, "番剧": 13, "国创": 167, "音乐": 3, "舞蹈": 129,
    "游戏": 4, "知识": 36, "科技": 188, "运动": 234, "汽车": 223,
    "生活": 160, "美食": 211, "动物": 217, "影视": 181, "娱乐": 5,
}


async def fetch_ranking(section: str = "全站", limit: int = 10) -> list:
    """排行榜。

    2026-09 服务器实测：ranking/v2 裸请求已被风控（-352），
    全站改走 popular 热门榜；分区走 wbi 签名的 ranking/v2。
    """
    rid = SECTIONS.get(section)
    if rid is None:
        raise BiliError(f"不支持的分区：{section}。可选：{'/'.join(SECTIONS)}")
    async with httpx.AsyncClient() as client:
        if rid == 0:
            data = await _jget(client, API_POPULAR, {"ps": 50, "pn": 1})
        else:
            k = await _get_mixin_key(client)
            params = _wbi_sign({"rid": rid, "type": "all"}, k)
            data = await _jget(client, API_RANKING, params)
        out = []
        for i, v in enumerate((data.get("list") or [])[:limit], 1):
            s = v.get("stat") or {}
            out.append({
                "rank": i,
                "title": v.get("title", ""),
                "owner": (v.get("owner") or {}).get("name", ""),
                "bvid": v.get("bvid", ""),
                "short": v.get("short_link_v2") or "",
                "view": s.get("view", 0),
                "like": s.get("like", 0),
            })
        return out

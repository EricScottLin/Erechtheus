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
import re
import time
import urllib.parse

import httpx
from nonebot.log import logger

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

API_VIDEO = "https://api.bilibili.com/x/web-interface/view"
API_CARD = "https://api.bilibili.com/x/web-interface/card"
API_RELATION = "https://api.bilibili.com/x/relation/stat"
API_RANKING = "https://api.bilibili.com/x/web-interface/ranking/v2"
API_POPULAR = "https://api.bilibili.com/x/web-interface/popular"
API_SEARCH_USER = "https://api.bilibili.com/x/web-interface/wbi/search/type"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"

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
_RISK_RETRY = 2


class BiliRiskError(BiliError):
    """撞上 B站风控（可重试 / 可降级）。"""

    def __init__(self, code: int = -352):
        super().__init__(RISK_TIP)
        self.code = code


def _today() -> int:
    return int(time.time()) // 86400


async def _get_mixin_key(client: httpx.AsyncClient) -> str:
    """取 wbi mixin_key，按天缓存。"""
    global _mixin_key, _mixin_key_day
    if _mixin_key and _mixin_key_day == _today():
        return _mixin_key
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


async def _jget(client: httpx.AsyncClient, url: str, params: dict,
                _try: int = 0) -> dict:
    """GET 并校验 B站业务码。

    对风控码（-352/-509/412）做指数退避重试：B站的风控多是"同一IP短时间
    请求过密"造成的瞬时拦截，退避 1.2s / 2.4s 后重试基本能过（2026-09 实测）。
    """
    try:
        r = await client.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        raise BiliError(f"网络请求失败：{type(e).__name__}")

    if r.status_code == 412:
        if _try < _RISK_RETRY:
            await asyncio.sleep(1.2 * (2 ** _try))
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
                await asyncio.sleep(1.2 * (2 ** _try))
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
    """视频详情：标题/简介/UP/播放/点赞/投币/收藏/分享/评论。"""
    async with httpx.AsyncClient() as client:
        if key == "url":
            resolved = await resolve_short_url(client, value)
            t = extract_target(resolved) if resolved else None
            if not t:
                raise BiliError("短链还原失败，换一条完整链接试试")
            key, value = t
        data = await _jget(client, API_VIDEO, {key: value})
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        return {
            "title": data.get("title", ""),
            "bvid": data.get("bvid", ""),
            "desc": (data.get("desc") or "").strip(),
            "pic": data.get("pic", ""),
            "pubdate": data.get("pubdate", 0),
            "duration": data.get("duration", 0),
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

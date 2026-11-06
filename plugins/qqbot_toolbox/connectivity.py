"""连通性检查（``/连通 [目标]``）：在容器里实际发 HTTP 请求，把结果回给群。

为什么做这个：服务器上排查网络时只能 SSH 上去敲 curl，
而"是不是机器人自己连不上"这类问题（比如模型端点挂了、曲库接口超时）
最好直接在群里就能问出来。

用法::

    /连通             检查所有预置端点
    /curl             同上（别名：/curl /网络检查 /连通性 /netcheck）
    /连通 mistral     按关键词只查匹配的端点
    /连通 <url>       直接测任意地址（只允许 http/https）
    /curl google.com  裸域名/主机名自动补 https:// 再测（像真 curl 一样）
    /curl 8.8.8.8:53  带端口也行（主机名:端口 → https://主机名:端口）

安全边界：

- 只允许 http/https，禁止 file://、ftp:// 之类
- 内网地址照测（排查内网服务也用得上），但**不跟随重定向到别的协议**
- 只发 GET，不读响应正文（只看状态码/耗时），不会把大文件拉进来
- 结果只回状态码与延迟，不回响应内容（避免把网页/密钥刷进群）
"""

import re
import time
from dataclasses import dataclass

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

def _cfg():
    """取父包的 plugin_config（它定义在 __init__ 里，不在 config.py）。

    延迟导入是为了避开「__init__ 导入本模块、本模块又导入 __init__」的循环。
    """
    from . import plugin_config

    return plugin_config


@dataclass(frozen=True)
class Target:
    """一个可检查的端点。"""

    key: str  # 关键词（用户用 /连通 这个词 来筛选）
    name: str  # 展示名
    url: str  # 实际请求的 URL
    note: str = ""  # 补充说明（比如"401 是正常的"）


# 预置端点：覆盖机器人实际依赖的外部服务。
# 注意保持"能一眼看出好坏"：状态码 + 延迟就够，不需要正文。
DEFAULT_TARGETS: tuple[Target, ...] = (
    Target("mistral", "Mistral 官方", "https://api.mistral.ai/v1/models", "401=通（没带 key 属正常）"),
    Target("mistral-web", "Mistral 官网", "https://mistral.ai"),
    Target("netease", "网易云（曲库/歌词）", "https://music.163.com/api/search/get?s=%E6%99%B4%E5%A4%A9&type=1&limit=1"),
    Target("lrclib", "lrclib（歌词库）", "https://lrclib.net/api/search?q=test"),
    Target("bilibili", "B 站（视频解析）", "https://api.bilibili.com/x/web-interface/nav"),
    Target("qqbot-gw", "当前模型网关", ""),  # url 运行时从配置读
)

# 非 http(s) 一律拒绝；也顺手挡住 shell 元字符（虽然我们不走 shell）
_URL_RE = re.compile(r"^https?://[^\s\"'`;|&<>]+$", re.IGNORECASE)

# 裸主机名 / 域名 / IP，可带端口：google.com、api.mistral.ai、8.8.8.8、example.com:8443
# 不含空白与 shell 元字符；至少两段（a.b）或纯 IPv4，避免把「你好」之类当成域名
_HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d{1,5})?$"
    r"|^(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?$",
    re.IGNORECASE,
)

# 单段主机名（/curl google）：只在 /curl 这种「就是 curl」的写法下才认，
# 免得 /连通 里随便一个词都被当成域名去联网
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?::\d{1,5})?$", re.IGNORECASE)


def _as_host_url(query: str, *, allow_single_label: bool = False) -> str:
    """把裸主机名补成 URL；不是合法主机名就返回空串。

    ``google.com`` → ``https://google.com``；``google.com:8080`` → ``https://google.com:8080``。
    ``allow_single_label=True`` 时 ``google`` 也算（→ ``https://google``）。
    """
    q = query.strip()
    if _HOST_RE.match(q) or (allow_single_label and _LABEL_RE.match(q)):
        return f"https://{q}"
    return ""


def _gateway_url() -> str:
    """当前 qqbot_ai 的模型网关地址（读不到就跳过这一项）。"""
    try:
        from nonebot import get_driver

        base = str(getattr(get_driver().config, "qqbot_ai_api_base", "") or "").rstrip("/")
        return f"{base}/models" if base else ""
    except Exception:  # noqa: BLE001
        return ""


def _materialize(targets: list[Target]) -> list[Target]:
    """补全运行时才知道的 URL（如模型网关），并丢掉拿不到地址的项。"""
    out: list[Target] = []
    for t in targets:
        url = t.url or (_gateway_url() if t.key == "qqbot-gw" else "")
        if url:
            out.append(Target(t.key, t.name, url, t.note))
    return out


def resolve_targets(query: str, *, raw_host: bool = False) -> tuple[list[Target], str]:
    """把用户输入解析成要检查的端点列表。

    返回 ``(targets, error)``；``error`` 非空表示输入非法，直接把这句话回给用户。

    ``raw_host=True``（由 ``/curl`` 触发）时，查不到预置关键词就把输入当主机名硬测，
    哪怕只有一段（``/curl google`` → ``https://google``），和真 curl 的直觉一致。
    """
    query = (query or "").strip()
    if not query:
        return _materialize(list(DEFAULT_TARGETS)), ""

    # 直接给 URL
    if "://" in query:
        if not _URL_RE.match(query):
            return [], "只支持 http:// 或 https:// 开头的地址。"
        return [Target(query, query[:60], query)], ""

    # 按关键词筛（先查预置关键词，保证 /curl mistral 仍走预置端点）
    low = query.lower()
    matched = [
        t for t in DEFAULT_TARGETS
        if low in t.key.lower() or low in t.name.lower()
    ]
    if matched:
        return _materialize(matched), ""

    # 裸主机名/域名/IP：像 curl 一样直接测（/curl google.com）
    host_url = _as_host_url(query, allow_single_label=raw_host)
    if host_url:
        return [Target(host_url, query[:60], host_url)], ""

    names = "、".join(t.key for t in DEFAULT_TARGETS if t.key != "qqbot-gw")
    return [], (
        f"没找到「{query}」。可用关键词：{names}；"
        "也可以直接给 http(s) 地址或域名，如 /curl google.com。"
    )


async def probe(url: str) -> tuple[bool, str]:
    """探一个地址；返回 ``(是否通, 给人看的结果)``。"""
    timeout = _cfg().qqbot_toolbox_conn_timeout
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            follow_redirects=False,
            trust_env=False,  # 不吃 HTTP_PROXY，测的是真实直连
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "qqbot-conncheck/1.0"})
        ms = (time.perf_counter() - started) * 1000
        code = resp.status_code
    except httpx.TimeoutException:
        ms = (time.perf_counter() - started) * 1000
        return False, f"超时 >{timeout:g}s"
    except httpx.ConnectError:
        ms = (time.perf_counter() - started) * 1000
        return False, f"连不上（{ms / 1000:.1f}s）"
    except httpx.HTTPError as e:
        ms = (time.perf_counter() - started) * 1000
        return False, f"失败：{type(e).__name__}（{ms / 1000:.1f}s）"

    # 3xx 不跟随：只报告"能连上但被重定向"
    if 300 <= code < 400:
        return True, f"HTTP {code} → {resp.headers.get('location', '?')[:40]}（{ms:.0f}ms）"
    ok = code < 400 or code in (401, 403)  # 401/403 说明服务可达，只是要鉴权
    return ok, f"HTTP {code}（{ms:.0f}ms）"


# ---------------------------------------------------------------- 命令
conn_cmd = on_command(
    "连通",
    aliases={"连通性", "网络检查", "curl", "网络连通", "连通检查", "netcheck"},
    priority=5,
    block=True,
)


@conn_cmd.handle()
async def handle_conn(event: MessageEvent, arg: Message = CommandArg()) -> None:
    """检查到各端点的连通性（在容器内实际发请求）。"""
    # 用 /curl 触发时按「真 curl」的直觉解析（裸词也当主机名）。
    # 用 get_plaintext() 拿整条消息（含指令名），别用 get_message() 的 str()，
    # 否则会拼出 "[CQ:...] /curl google" 这种带段前缀的串，判断必然落空。
    raw_host = event.get_plaintext().lstrip().lower().startswith(("curl", "/curl"))
    targets, error = resolve_targets(arg.extract_plain_text(), raw_host=raw_host)
    if error:
        await conn_cmd.finish(error)

    lines = ["🌐 连通性检查（在容器内实测）"]
    for t in targets:
        ok, detail = await probe(t.url)
        mark = "✅" if ok else "❌"
        line = f"{mark} {t.name}：{detail}"
        if t.note:
            line += f"（{t.note}）"
        lines.append(line)

    bad = sum(1 for line in lines[1:] if line.startswith("❌"))
    lines.append(f"────────────\n{len(targets) - bad}/{len(targets)} 通")
    await conn_cmd.finish("\n".join(lines))

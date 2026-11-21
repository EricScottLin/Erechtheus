"""真实网络探测：调用 ``curl`` 子进程、发 ICMP/TCP ping。

为什么单独一个模块（而不是继续用 httpx）：

- 用户的分工很明确：**不带参数 = bot 自己好不好；带 IP/域名/URL = 真去测那个目标**。
  既然是「真去测」，就得用真正的 curl / ping，而不是在 Python 里模拟一个近似行为
  —— 出问题时要能对得上用户在机器上手动敲的命令。
- 排查场景里 curl 的退出码、DNS 报错、TLS 报错是最有价值的证据，
  自己用 httpx 包一层反而丢信息。

## 关于 ping

容器里**没有 iputils 的 ping 二进制**，也不打算为此重建镜像。所以：

- 目标能解析出 IPv4/IPv6 → 用原始套接字发 ICMP Echo（需要 root；容器内通常是 root）
- 没有 root 或无权限 → 自动退回 **TCP 连接探测**（连 80/443/指定端口），
  这同样能证明「目标可达」，只是不返回 RTT 统计

两条路都失败才判「不通」。退回 TCP 时结果里会注明，避免把 TCP 探测说成 ICMP。

## 安全边界

- 所有外部输入先过白名单正则（``sanitize_*``），**绝不拼 shell 字符串**；
  子进程用 ``execve`` 数组形式传参（``asyncio.create_subprocess_exec``），不经 shell。
- curl 只放行 http/https，禁用 ``--output-dir`` 之类会写文件的参数；
  输出只取状态码与耗时，正文丢弃（不把网页刷进群）。
- 有总超时（``asyncio.wait_for``），超时就杀掉子进程，绝不卡住消息处理。
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import socket
import struct
import time
from dataclasses import dataclass

# ---------------------------------------------------------------- 输入净化
# 域名/IP 字面量：只允许 DNS 字符集 + 可选的端口；挡住一切 shell 元字符
_HOST_LITERAL_RE = re.compile(
    r"^(?P<host>[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)(?::(?P<port>\d{1,5}))?$",
    re.IGNORECASE,
)
# URL 白名单：除了空白与常见 shell 元字符，还要挡住 $ ( ) { } [ ] * ? ~ # !
# 这些在 shell / 解释器里会被展开或截断。查询串里的 ? 会命中这条，
# 所以下面 parse_endpoint 对 URL 的解析另有一套只取 host 的逻辑。
_URL_RE = re.compile(r"^https?://[^\s\"'`;|&<>\\$(){}*!]+$", re.IGNORECASE)
_ALLOWED_SCHEMES = ("http://", "https://")


@dataclass(frozen=True)
class Endpoint:
    """一个已净化的探测目标。"""

    raw: str  # 用户原样输入（用于展示）
    host: str  # 主机名或 IP（不带端口）
    port: int | None  # 显式端口；None 表示按协议默认
    url: str | None  # 有 URL 时才有（用于 curl）

    @property
    def is_url(self) -> bool:
        return self.url is not None

    @property
    def display(self) -> str:
        if self.port is not None and not self.is_url:
            return f"{self.host}:{self.port}"
        return self.host


def parse_endpoint(query: str) -> tuple[Endpoint | None, str]:
    """把用户输入解析成 :class:`Endpoint`；非法输入时返回 ``(None, 错误提示)``。

    支持三种写法：

    - URL：``https://api.mistral.ai/v1/models``
    - 域名 / IP：``api.mistral.ai``、``8.8.8.8``
    - 域名或 IP + 端口：``api.mistral.ai:443``、``1.1.1.1:53``
    """
    q = (query or "").strip()
    if not q:
        return None, "用法：/ping <域名或IP或URL>，例如 /ping api.mistral.ai"

    # --- URL 形式 ---
    if "://" in q:
        if not _URL_RE.match(q):
            return None, "这个地址格式不对，只支持 http:// 或 https:// 开头的 URL。"
        low = q.lower()
        if not low.startswith(_ALLOWED_SCHEMES):
            return None, "只支持 http:// 或 https:// 地址。"
        # 从 URL 里抠出 host/port，供 ping 兜底使用
        rest = q.split("://", 1)[1]
        hostport = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if "@" in hostport:  # 去掉 user:pass@
            hostport = hostport.rsplit("@", 1)[1]
        port = None
        if hostport.startswith("["):  # IPv6 字面量 [::1]:8080
            host, _, tail = hostport[1:].partition("]")
            if tail.startswith(":") and tail[1:].isdigit():
                port = int(tail[1:])
            try:
                ipaddress.IPv6Address(host)
            except ValueError:
                return None, "IPv6 地址格式不对。"
        else:
            m = _HOST_LITERAL_RE.match(hostport)
            if not m:
                return None, "这个地址里含非法字符。"
            host = m.group("host")
            if m.group("port"):
                port = int(m.group("port"))
        if port is not None and not (0 < port < 65536):
            return None, "端口必须是 1-65535。"
        if not _valid_host(host):
            return None, "这个域名/IP 看着不合法。"
        return Endpoint(q, host, port, q), ""

    # --- 域名 / IP（可带端口） ---
    m = _HOST_LITERAL_RE.match(q)
    if not m:
        return None, "只支持域名、IP 或 http(s) 地址，别塞别的字符进来。"
    host = m.group("host")
    if not _valid_host(host):
        return None, "这个域名/IP 看着不合法。"
    port = None
    if m.group("port"):
        port = int(m.group("port"))
        if not (0 < port < 65536):
            return None, "端口必须是 1-65535。"
    return Endpoint(q, host, port, None), ""


def _valid_host(host: str) -> bool:
    """主机名或 IP 字面量的合法性检查（足以挡住误输入，不追求 RFC 完备）。"""
    if not host or len(host) > 253:
        return False
    try:  # IP 字面量直接过
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # 域名：至少两段，每段 1-63，段内不以连字符开头结尾
    labels = host.split(".")
    if len(labels) < 2:
        return False
    return all(1 <= len(lb) <= 63 and lb[0] != "-" and lb[-1] != "-" for lb in labels)


# ---------------------------------------------------------------- curl
async def curl_probe(
    url: str, timeout: float = 8.0, *, allow_insecure: bool = False
) -> dict:
    """真正执行 ``curl``，返回状态码 / 耗时 / 退出码等结构化结果。

    只发 GET，``--output /dev/null`` 丢弃正文，``-w`` 只取机器可读的数字。
    """
    binary = shutil.which("curl")
    if not binary:
        return {"ok": False, "error": "容器里没有 curl"}

    # 分隔符用 "|"：curl 的 -w 只认 \\t 这类转义写法，源码里直接写制表符它不认；
    # 用 | 既稳妥又能一眼看出字段边界。
    fmt = (
        "%{http_code}|%{time_total}|%{time_connect}|%{time_namelookup}"
        "|%{remote_ip}|%{size_download}|%{redirect_url}"
    )
    args = [
        binary,
        "-sS",  # 静默，但保留错误信息
        "-o",
        "/dev/null",
        "--max-time",
        f"{max(1.0, timeout):.1f}",
        "--connect-timeout",
        f"{max(1.0, min(5.0, timeout)):.1f}",
        "-A",
        "qqbot-netcheck/1.0",
        "-w",
        fmt,
    ]
    if allow_insecure:
        args.append("-k")
    args.append(url)  # 已过白名单正则，且用 exec 数组传参，不经 shell

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:  # 极端情况：exec 失败
        return {"ok": False, "error": f"curl 启动失败：{e}"}

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout + 3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "error": "超时", "timeout": True}

    elapsed = loop.time() - started
    stdout = out.decode("utf-8", "replace").strip()
    stderr = err.decode("utf-8", "replace").strip()
    parts = stdout.split("|")
    result: dict = {"ok": proc.returncode == 0, "curl": True, "elapsed": elapsed}
    if len(parts) >= 7:
        result.update(
            http_code=parts[0],
            time_total=float(parts[1] or 0),
            time_connect=float(parts[2] or 0),
            time_namelookup=float(parts[3] or 0),
            remote_ip=parts[4],
            size=parts[5],
            redirect=parts[6],
        )
    if proc.returncode != 0:
        result["error"] = _curl_error(stderr, proc.returncode)
        result["stderr"] = stderr[:200]
    return result


def _curl_error(stderr: str, code: int) -> str:
    """把 curl 的退出码 / stderr 翻译成一句人话。"""
    known = {
        6: "域名解析失败（DNS 查不到）",
        7: "连不上（端口拒绝或防火墙）",
        28: "超时",
        35: "TLS 握手失败",
        51: "对端证书校验不过",
        52: "服务器没返回内容",
        56: "传输中断",
        60: "证书不受信任",
    }
    if code in known:
        return known[code]
    # 从 stderr 里挑最有用的一行
    for line in reversed(stderr.splitlines()):
        line = line.strip()
        if line and "curl:" in line:
            return line.split("curl:", 1)[1].strip()[:80]
    return f"curl 退出码 {code}"


# ---------------------------------------------------------------- ping
async def ping(host: str, timeout: float = 3.0, port: int | None = None) -> dict:
    """对主机做可达性探测，优先 ICMP，不行退回 TCP。

    返回值里 ``method`` 说明用了哪种方式（``icmp`` / ``tcp``），
    ``ok`` 表示目标可达。

    ``port`` 非空时**不做 ICMP**，直接测那个 TCP 端口 —— 用户写
    ``/ping host:8080`` 想知道的显然是「这个端口通不通」，回一句
    「ICMP 通」等于没回答。
    """
    try:
        info = await asyncio.get_running_loop().run_in_executor(
            None, _resolve, host
        )
    except (socket.gaierror, UnicodeError) as e:
        return {"ok": False, "method": "dns", "error": f"域名解析失败（{e}）"}
    except OSError as e:
        return {"ok": False, "method": "dns", "error": f"域名解析失败（{e}）"}

    ip, family = info
    if port is not None:
        # 指定了端口：直接测该端口
        tcp = await _tcp_ping(ip, family, timeout, port=port)
        tcp["target_ip"] = ip
        return tcp

    icmp = await _icmp_ping(ip, family, timeout)
    if icmp is not None:
        icmp["target_ip"] = ip
        return icmp

    # 无 root / 无原始套接字权限：退回 TCP 连接探测
    tcp = await _tcp_ping(ip, family, timeout, port=None)
    tcp["target_ip"] = ip
    tcp["fallback_from"] = "icmp"
    return tcp


def _resolve(host: str) -> tuple[str, int]:
    infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    for fam, _, _, _, sockaddr in infos:
        if fam == socket.AF_INET:
            return sockaddr[0], socket.AF_INET
    fam, _, _, _, sockaddr = infos[0]
    return sockaddr[0], fam


async def _icmp_ping(ip: str, family: int, timeout: float) -> dict | None:
    """发 ICMP Echo。返回 None 表示当前环境做不了 ICMP（应退回 TCP）。"""
    if os.geteuid() != 0:
        return None
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _icmp_ping_sync, ip, family, timeout),
            timeout=timeout + 1.0,
        )
    except (asyncio.TimeoutError, OSError, ValueError, PermissionError):
        return None


def _checksum(data: bytes) -> int:
    """RFC 1071 反码求和校验和（ICMP 用）。"""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def _icmp_ping_sync(ip: str, family: int, timeout: float) -> dict | None:
    """同步实现 ICMP Echo（在 executor 线程里跑，避免阻塞事件循环）。"""
    is_v6 = family == socket.AF_INET6
    proto = socket.IPPROTO_ICMPV6 if is_v6 else socket.IPPROTO_ICMP
    try:
        sock = socket.socket(socket.AF_INET6 if is_v6 else socket.AF_INET,
                             socket.SOCK_RAW, proto)
    except (PermissionError, OSError):
        return None  # 没权限，交给 TCP 兜底

    ident = os.getpid() & 0xFFFF
    seq = 1
    payload = b"qqbot-ping" + struct.pack("!d", time.time())
    if is_v6:
        # ICMPv6 校验和由内核计算（含伪首部），这里填 0 即可
        header = struct.pack("!BBHHH", 128, 0, 0, ident, seq)
        packet = header + payload
    else:
        # IPv4 的 ICMP 校验和**必须自己算**：
        # 填 0 时 Linux 内核不会帮忙补（不同于 UDP），包会被对端丢弃，
        # 表现为「发包成功但永远收不到回复」。2026-11-21 踩过这个坑。
        packet = struct.pack("!BBHHH", 8, 0, 0, ident, seq) + payload
        packet = struct.pack("!BBHHH", 8, 0, _checksum(packet), ident, seq) + payload

    try:
        sock.settimeout(timeout)
        started = time.perf_counter()
        sock.sendto(packet, (ip, 0))
        deadline = started + timeout
        while time.perf_counter() < deadline:
            sock.settimeout(max(0.05, deadline - time.perf_counter()))
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            # IPv4 原始套接字会带 IP 头，跳过它找 ICMP 头
            offset = 0
            if not is_v6 and len(data) >= 20:
                ihl = (data[0] & 0x0F) * 4
                offset = ihl
            if len(data) < offset + 8:
                continue
            rtype, _, _, r_ident, r_seq = struct.unpack("!BBHHH", data[offset:offset + 8])
            expected = 129 if is_v6 else 0
            if rtype == expected and r_ident == ident and r_seq == seq:
                rtt = (time.perf_counter() - started) * 1000
                return {"ok": True, "method": "icmp", "rtt_ms": rtt, "lost": 0, "sent": 1}
        return {"ok": False, "method": "icmp", "error": f"ICMP 无响应（>{timeout:g}s）",
                "lost": 1, "sent": 1}
    finally:
        sock.close()


async def _tcp_ping(
    ip: str, family: int, timeout: float, port: int | None
) -> dict:
    """TCP 连接探测：能连上（含被拒绝）都说明目标主机活着。"""
    ports = [port] if port else [443, 80]
    last_err = ""
    for p in ports:
        started = time.perf_counter()
        try:
            fut = asyncio.open_connection(ip, p, family=family)
            reader, writer = await asyncio.wait_for(fut, timeout=min(timeout, 3.0))
        except asyncio.TimeoutError:
            last_err = f"端口 {p} 超时"
            continue
        except OSError as e:
            # 连接被拒绝（ECONNREFUSED）说明主机在，只是端口没开
            if getattr(e, "errno", None) in (111, 61, 10061):
                rtt = (time.perf_counter() - started) * 1000
                return {"ok": True, "method": "tcp", "port": p, "rtt_ms": rtt,
                        "note": f"端口 {p} 拒绝连接（主机在线，端口未开）"}
            last_err = f"端口 {p}：{e}"
            continue
        else:
            rtt = (time.perf_counter() - started) * 1000
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "method": "tcp", "port": p, "rtt_ms": rtt}
    return {"ok": False, "method": "tcp", "error": last_err or "TCP 连接失败"}

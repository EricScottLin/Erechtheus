#!/usr/bin/env python3
"""诊断：监听私聊二维码消息，秒级试多种认证参数组合并记录 errorID。

只在排查"二维码认证失败"时手动运行；不参与生产逻辑。
用法：python scripts/probe-qr-live.py <OB11_ACCESS_TOKEN>
"""

import asyncio
import hashlib
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import websockets
import zxingcpp
from PIL import Image

WS = "ws://127.0.0.1:3001"
OWNER = "1943546421"
SECRET = "XcW5FW4cPArBXEk4vzKz3CIrMuA5EVVW"
URL = "http://ai.sys-allnet.cn/wc_aime/api/get_data"
CHIPS = {"默认 keychip": "A63E-01C28055905", "表内真实 keychip": "A63E01C2562"}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


def auth(chip, tz_hours, code, *, json_header=True):
    ts = datetime.now(timezone(timedelta(hours=tz_hours))).strftime("%y%m%d%H%M%S")
    key = hashlib.sha256((chip + ts + SECRET).encode()).hexdigest().upper()
    payload = {"chipID": chip, "openGameID": "MAID", "key": key, "qrCode": code, "timestamp": ts}
    headers = {"User-Agent": "WC_AIME_LIB", "Host": "ai.sys-all.cn"}
    if json_header:
        headers["Content-Type"] = "application/json"
    else:
        headers["Contention"] = "Keep-Alive"
    req = urllib.request.Request(
        URL, data=json.dumps(payload, separators=(",", ":")).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def probe(qr_text):
    embedded = qr_text[9:21] if len(qr_text) >= 21 else "?"
    log(f"二维码长度 {len(qr_text)}，内置时间戳 {embedded}，本地时间 {datetime.now().strftime('%y%m%d%H%M%S')}")
    variants = [
        ("默认chip / 北京时区 / 后64位", CHIPS["默认 keychip"], 8, qr_text[-64:], True),
        ("默认chip / 东京时区 / 后64位", CHIPS["默认 keychip"], 9, qr_text[-64:], True),
        ("表内chip / 北京时区 / 后64位", CHIPS["表内真实 keychip"], 8, qr_text[-64:], True),
        ("表内chip / 东京时区 / 后64位", CHIPS["表内真实 keychip"], 9, qr_text[-64:], True),
        ("默认chip / 北京时区 / 整串84", CHIPS["默认 keychip"], 8, qr_text, True),
        ("表内chip / 北京时区 / 整串84", CHIPS["表内真实 keychip"], 8, qr_text, True),
        ("默认chip / 北京时区 / 参考版请求头", CHIPS["默认 keychip"], 8, qr_text[-64:], False),
    ]
    for label, chip, tz, code, jh in variants:
        try:
            data = auth(chip, tz, code, json_header=jh)
            uid, token = data.get("userID"), data.get("token")
            log(f"{label}: errorID={data.get('errorID')} userID={uid} token={'有' if token else '无'}")
            if uid not in (None, -1) and token:
                log(f"*** 成功组合：{label} ***")
                return label
        except Exception as exc:
            log(f"{label}: 异常 {type(exc).__name__}: {str(exc)[:80]}")
    return None


async def main():
    token = sys.argv[1]
    log("监听私聊二维码消息中…")
    async with websockets.connect(WS, additional_headers={"Authorization": f"Bearer {token}"}) as ws:
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3600)
            except asyncio.TimeoutError:
                log("等待超时，退出")
                return
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if event.get("post_type") != "message" or event.get("message_type") != "private":
                continue
            if str(event.get("user_id")) != OWNER:
                continue
            images = [seg for seg in event.get("message", []) if seg.get("type") == "image"]
            if not images:
                continue
            url = images[0].get("data", {}).get("url") or ""
            log(f"收到二维码消息，开始下载 {url[:60]}…")
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = resp.read()
                text = (zxingcpp.read_barcodes(Image.open(io.BytesIO(data)))[0].text or "").strip()
            except Exception as exc:
                log(f"下载/解码失败：{type(exc).__name__}")
                continue
            probe(text)


if __name__ == "__main__":
    asyncio.run(main())

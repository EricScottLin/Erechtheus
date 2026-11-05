"""歌词判定：问模型「这像不像歌词」+ 猜歌名（可选，失败时退回本地版式）。

实测（deepseek 系模型、经项目现有 OpenAI 兼容网关）：

- **一次判一条**：2~3 秒，稳定，能给歌名/歌手；
- **一次判一批**：推理链长、偶尔直接把 token 吃光返回空；加「直接给答案」后
  5~10 秒判 5 条，且分类很稳（歌词 / 台词 / 聊天 三类都分对了）。

因此这里采用「攒批 + 单条兜底」：群里本该 1.5 秒内攒齐一批再发一次请求；
单批超时或解析失败时，逐条重试一次（单条请求便宜且稳）。

注意：模型给的是**线索**（歌名/歌手），不是真相 —— 真正的歌词以曲库为准，
见 :mod:`search` 的 ``hint_bonus``。
"""
# 作为独立顶层包导入时（单测 `import qqbot_lyric.xxx`）不允许，走插件包 __init__
if not (__package__ or "").endswith("qqbot_lyric"):
    raise ImportError("qqbot_lyric 子模块只能以 qqbot_lyric.<module> 形式导入")


import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from nonebot.log import logger

KIND_LYRIC = "LYRIC"
KIND_QUOTE = "QUOTE"
KIND_CHAT = "CHAT"

_SYSTEM = "你是歌词识别器。只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。输出前不要分析。"
_PROMPT = """逐条判断消息类型：
LYRIC = 一首歌的歌词片段（含副歌、说唱、儿歌、民谣、童谣）
QUOTE = 台词、诗、名人语录、广告语
CHAT = 普通群聊闲聊
直接给出答案，不要逐个分析。

输出格式：{{"items":[{{"i":序号,"type":"LYRIC/QUOTE/CHAT","title":"歌名","artist":"歌手"}}]}}
title/artist 只在确定时填写（LYRIC 且你确定是哪首歌），否则留空。

{lines}"""


class LLMError(Exception):
    """模型调用失败（网络、鉴权、限流、返回格式异常）。"""


@dataclass
class Verdict:
    """一条消息的判定结果。"""

    index: int
    kind: str = KIND_CHAT
    title: str = ""
    artist: str = ""
    source: str = "llm"  # llm / local / cache

    @property
    def is_lyric(self) -> bool:
        return self.kind == KIND_LYRIC


@dataclass
class _Pending:
    """攒批中的一条判定请求。"""

    key: str  # 去重键（群 + 文本）
    text: str
    future: asyncio.Future = field(default_factory=asyncio.Future)
    ts: float = field(default_factory=time.time)


def parse_batch(payload: str, count: int) -> list[Verdict]:
    """从模型输出里抽出 JSON 列表；宽容处理代码块、前后废话、缺项。"""
    text = (payload or "").strip()
    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return []
    try:
        obj = json.loads(match.group(0))
    except ValueError:
        return []
    items = obj.get("items") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return []
    out: list[Verdict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < count:
            continue
        kind = str(item.get("type") or item.get("kind") or "").strip().upper()
        if kind not in (KIND_LYRIC, KIND_QUOTE, KIND_CHAT):
            continue
        out.append(
            Verdict(
                index=idx,
                kind=kind,
                title=str(item.get("title") or "").strip(),
                artist=str(item.get("artist") or "").strip(),
            )
        )
    return out


class LLMJudge:
    """OpenAI 兼容接口的歌词判定器（自带攒批与单条兜底）。"""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout: float = 15.0,
        max_tokens: int = 800,
        batch_size: int = 6,
        batch_wait: float = 1.5,
        disabled: bool = False,
    ) -> None:
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.batch_size = max(1, batch_size)
        self.batch_wait = max(0.0, batch_wait)
        self.disabled = disabled
        self._queue: list[_Pending] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self.stats = {"calls": 0, "items": 0, "fail": 0}

    @property
    def configured(self) -> bool:
        return bool(not self.disabled and self.api_key and self.model and self.api_base)

    # ------------------------------------------------------------- 对外入口
    async def judge_many(self, texts: list[str]) -> list[Verdict]:
        """判定一批文本，返回与输入等长的结果（失败的位置给「非歌词」）。"""
        if not texts:
            return []
        if not self.configured:
            return [Verdict(index=i, kind=KIND_CHAT, source="local") for i in range(len(texts))]

        # 同文本只判一次
        uniq: list[str] = list(dict.fromkeys(texts))
        futures: list[asyncio.Future] = [asyncio.get_running_loop().create_future() for _ in uniq]
        pendings = [
            _Pending(key=f"{idx}:{text}", text=text, future=fut) for idx, (text, fut) in enumerate(zip(uniq, futures))
        ]
        async with self._lock:
            self._queue.extend(pendings)
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_loop())

        verdicts = await asyncio.gather(
            *[_settle(f) for f in futures], return_exceptions=False
        )
        by_text = {text: verdicts[idx] for idx, text in enumerate(uniq)}
        out: list[Verdict] = []
        for idx, text in enumerate(texts):
            verdict = by_text.get(text) or Verdict(index=idx)
            out.append(Verdict(index=idx, kind=verdict.kind, title=verdict.title,
                               artist=verdict.artist, source=verdict.source))
        return out

    async def _flush_loop(self) -> None:
        """攒批：够 ``batch_size`` 或等满 ``batch_wait`` 就发一次。"""
        deadline = time.monotonic() + self.batch_wait
        while True:
            async with self._lock:
                if not self._queue:
                    return
                remaining = deadline - time.monotonic()
                if len(self._queue) >= self.batch_size or remaining <= 0:
                    batch = self._queue[: self.batch_size]
                    self._queue = self._queue[len(batch) :]
                    deadline = time.monotonic() + self.batch_wait
                else:
                    batch = None
            if batch is None:
                await asyncio.sleep(min(0.1, max(0.01, remaining)))
                continue
            try:
                await self._answer(batch)
            except Exception as e:  # noqa: BLE001 —— 兜底：任何异常都不能让等待方挂死
                logger.warning(f"[qqbot_lyric] 判定批次异常：{type(e).__name__} {e}")
                for item in batch:
                    if not item.future.done():
                        item.future.set_result(Verdict(index=0, source="local"))

    async def _answer(self, batch: list[_Pending]) -> None:
        texts = list(dict.fromkeys(item.text for item in batch))
        verdicts = await self._request(texts)
        if verdicts is None:
            verdicts = []
            for text in texts:
                single = await self._request([text])
                verdicts.append(
                    single[0] if single else Verdict(index=0, kind=KIND_CHAT, source="local")
                )
        by_text = dict(zip(texts, verdicts))
        for item in batch:
            if item.future.done():
                continue
            item.future.set_result(by_text.get(item.text) or Verdict(index=0, source="local"))

    # ------------------------------------------------------------- HTTP
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout + 5, connect=5.0),
                headers=headers,
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _request(self, texts: list[str]) -> Optional[list[Verdict]]:
        """发一次请求；返回 ``None`` 表示失败（由调用方逐条重试）。"""
        lines = "\n".join(f"{i}. {text}" for i, text in enumerate(texts))
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT.format(lines=lines)},
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        self.stats["calls"] += 1
        try:
            resp = await self._get_client().post(
                f"{self.api_base}/chat/completions", json=payload, timeout=self.timeout
            )
        except httpx.HTTPError as e:
            logger.debug(f"[qqbot_lyric] 模型请求失败：{type(e).__name__}")
            self.stats["fail"] += 1
            return None
        if resp.status_code != 200:
            logger.debug(f"[qqbot_lyric] 模型返回 HTTP {resp.status_code}")
            self.stats["fail"] += 1
            return None
        try:
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            self.stats["fail"] += 1
            return None
        content = message.get("content") or ""
        parsed = parse_batch(content if isinstance(content, str) else "", len(texts))
        if not parsed and len(texts) > 1:
            # 批请求空回复（推理把 token 吃光，长文本尤其常见）：逐条重来
            logger.debug(f"[qqbot_lyric] 批请求空回复（{len(texts)} 条），改为逐条请求")
            return None
        if not parsed:
            logger.debug(
                f"[qqbot_lyric] 模型输出无法解析（{len(content)} 字，"
                f"finish={choice.get('finish_reason')}）"
            )
            self.stats["fail"] += 1
            return None
        self.stats["items"] += len(parsed)
        by_idx = {v.index: v for v in parsed}
        out: list[Verdict] = []
        for idx in range(len(texts)):
            verdict = by_idx.get(idx) or Verdict(index=idx, kind=KIND_CHAT, source="local")
            verdict.index = idx
            out.append(verdict)
        return out


async def _settle(future: asyncio.Future) -> Verdict:
    """等一个 future；被取消/超时时给个「非歌词」。"""
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=180)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return Verdict(index=0, kind=KIND_CHAT, source="local")

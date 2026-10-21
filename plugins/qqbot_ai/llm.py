"""OpenAI 兼容 Chat Completions 客户端（纯 asyncio，不额外引入 SDK）。

可直接对接 DeepSeek / OpenAI / 通义 / Moonshot / OneAPI / Ollama 等任何
实现 ``POST {api_base}/chat/completions`` 的服务。
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from nonebot.log import logger


class LLMError(Exception):
    """模型调用失败（网络、鉴权、限流、返回格式异常）。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    reasoning: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_truncated(self) -> bool:
        """max_tokens 用尽且正文为空 —— 思考把额度吃光了（带 reasoning 的模型常见）。"""
        return not self.content and not self.tool_calls and self.finish_reason == "length"


def _parse_arguments(raw: str) -> dict[str, Any]:
    """解析工具参数 JSON；模型偶尔会给出空串或坏 JSON，宽容处理。"""
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        # 有些模型会在 JSON 外包一层 ```json ... ```
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            value = json.loads(cleaned)
        except ValueError:
            logger.warning(f"[qqbot_ai] 工具参数不是合法 JSON，忽略：{raw[:100]}")
            return {}
    return value if isinstance(value, dict) else {}


def parse_response(payload: dict[str, Any]) -> ChatResult:
    """从 OpenAI 格式响应里取出正文与 tool_calls。"""
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"模型返回格式异常：{str(payload)[:200]}") from e

    content = message.get("content") or ""
    if isinstance(content, list):  # 个别服务把 content 返回成分段列表
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    finish_reason = str((payload["choices"][0] or {}).get("finish_reason") or "")

    calls: list[ToolCall] = []
    for idx, item in enumerate(message.get("tool_calls") or []):
        fn = item.get("function") or {}
        raw_args = fn.get("arguments") or ""
        if not isinstance(raw_args, str):
            raw_args = json.dumps(raw_args, ensure_ascii=False)
        calls.append(
            ToolCall(
                id=str(item.get("id") or f"call_{idx}"),
                name=str(fn.get("name") or ""),
                arguments=_parse_arguments(raw_args),
                raw_arguments=raw_args,
            )
        )
    return ChatResult(
        content=content,
        tool_calls=calls,
        finish_reason=finish_reason,
        reasoning=reasoning if isinstance(reasoning, str) else "",
    )


class LLMClient:
    """轻量客户端：一个实例复用一个 AsyncClient 连接池。"""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        temperature: float = 0.7,
        max_tokens: int = 512,
        extra_body: Optional[dict[str, Any]] = None,
        vision_model: str = "",
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        # 看图用的模型；留空沿用主模型
        self.vision_model = vision_model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = dict(extra_body or {})
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def url(self) -> str:
        return f"{self.api_base}/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                headers=headers,
                # 客户端级超时放宽到 10 分钟；真正的超时由每次请求的 timeout 参数控制
                timeout=httpx.Timeout(600.0, connect=10.0),
                trust_env=False,  # httpx 会吞掉 HTTP_PROXY 等环境变量，显式关掉更可预期
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(self.extra_body)
        return payload

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ChatResult:
        """调用一次 chat/completions，返回正文或工具调用请求。

        ``model`` 可覆盖本次请求的模型（看图时用视觉模型），
        ``timeout`` 可覆盖单次超时（看图单独放宽）。
        """
        payload = self.build_payload(messages, tools, max_tokens, temperature, model)
        client = self._get_client()
        try:
            resp = await client.post(
                self.url, json=payload, timeout=timeout or self.timeout
            )
        except httpx.TimeoutException as e:
            raise LLMError(
                "模型响应超时（看图这类任务较慢，可调大 qqbot_ai_vision_timeout_seconds）"
            ) from e
        except (httpx.ReadError, httpx.RemoteProtocolError) as e:
            # 服务端在响应过程中断开（上游模型超时/网关掐断）。视觉请求最容易触发，
            # 因为单次请求可能要一两分钟，超过网关的空闲超时就会被掐。
            raise LLMError("模型服务中途断开了连接（通常是任务太重超时），稍后再试") from e
        except httpx.HTTPError as e:
            raise LLMError(f"连接模型服务失败：{type(e).__name__}") from e

        if resp.status_code != 200:
            detail = resp.text[:300]
            if resp.status_code in (401, 403):
                raise LLMError("API Key 无效或无权限，请检查配置")
            if resp.status_code == 429:
                raise LLMError("模型服务限流，稍后再试")
            raise LLMError(f"模型服务返回 HTTP {resp.status_code}：{detail}")
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMError("模型服务返回的不是 JSON") from e
        return parse_response(data)


async def probe(client: LLMClient) -> str:
    """连通性自检：发一句最短的问候，返回模型回显。"""
    result = await asyncio.wait_for(
        client.chat([{"role": "user", "content": "ping"}], max_tokens=8),
        timeout=client.timeout,
    )
    return result.content or "(空回复)"


# ---------------------------------------------------------------- 图片消息
def build_image_message(text: str, data_urls: list[str]) -> dict[str, Any]:
    """拼一条「文本 + 多张图」的 user 消息（OpenAI vision 格式）。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for url in data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": content}


def has_images(messages: list[dict[str, Any]]) -> bool:
    """消息列表里是否含图片（决定要不要切视觉模型）。"""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            return True
    return False


def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """读取 PNG/JPEG/WebP 的像素尺寸；失败返回 ``None``。"""
    import struct

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        if data[12:16] == b"VP8X":
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return w, h
        if data[12:16] == b"VP8 ":
            w = int.from_bytes(data[26:28], "little") & 0x3FFF
            h = int.from_bytes(data[28:30], "little") & 0x3FFF
            return w, h
        if data[12:16] == b"VP8L":
            b = data[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return w, h
        return None
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            i += 2 + seg_len
        return None
    return None

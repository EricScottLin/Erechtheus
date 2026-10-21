"""工具定义与调度（模型 function calling 的「工具」层）。

工具统一用 OpenAI function 格式声明::

    {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

新增工具的步骤：

1. 实现一个 ``async def run_xxx(args: dict) -> str``，返回**给模型看的纯文本**；
2. 写一个 ``Tool(name, description, parameters, handler)`` 并放进 ``build_tools()``。

工具执行失败不抛异常，而是返回一段描述失败原因的文字，让模型自己决定
如何向用户兜底（例如说「没查到」），这样单次工具故障不会打断整段回答。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from nonebot.log import logger

ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]

CST = timezone(timedelta(hours=8))


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------- 单个工具实现
async def run_get_time(args: dict[str, Any]) -> str:
    now = datetime.now(CST)
    weekdays = "一二三四五六日"
    return f"当前北京时间：{now:%Y-%m-%d %H:%M:%S}，星期{weekdays[now.weekday()]}"


def build_tools(search_fn: Optional[ToolHandler], max_results: int) -> list[Tool]:
    """组装当前可用的工具列表。

    Args:
        search_fn: 联网搜索实现；``None`` 表示搜索后端不可用（不暴露该工具）
        max_results: 一次搜索最多返回几条
    """
    tools = [
        Tool(
            name="get_current_time",
            description="获取当前北京时间（日期、时间、星期）。用户问今天几号、现在几点、星期几时使用。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=run_get_time,
        )
    ]
    if search_fn is not None:
        tools.append(
            Tool(
                name="web_search",
                description=(
                    "联网搜索互联网，用于回答需要事实核查或时效性的问题："
                    "术语/梗/网络黑话是什么意思、是什么、最新新闻、价格、版本、人物等。"
                    "返回若干条标题、摘要与链接。回答时请依据摘要概括，不要照搬。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词，中文问题用中文关键词，可多写几个词",
                        },
                        "site": {
                            "type": "string",
                            "description": "可选，限定站点，如 zhihu.com、baike.baidu.com",
                        },
                    },
                    "required": ["query"],
                },
                handler=search_fn,
            )
        )
    return tools


# ---------------------------------------------------------------- 调度
async def execute_tool(
    tool: Tool,
    arguments: dict[str, Any],
    max_chars: int,
) -> str:
    """执行一个工具并把结果裁剪到 ``max_chars``。异常也转成文字返回。"""
    try:
        result = await tool.handler(arguments)
    except asyncio.TimeoutError:
        logger.warning(f"[qqbot_ai] 工具 {tool.name} 超时")
        return f"工具 {tool.name} 超时，暂时不可用。可以告诉用户稍后再问。"
    except Exception as e:  # noqa: BLE001 —— 工具故障不应打断整段对话
        logger.warning(f"[qqbot_ai] 工具 {tool.name} 执行失败：{type(e).__name__}: {e}")
        return f"工具 {tool.name} 执行失败（{type(e).__name__}），无法获取结果。"
    text = (result or "").strip()
    if not text:
        return f"工具 {tool.name} 没有返回任何内容。"
    if len(text) > max_chars:
        text = text[:max_chars] + "…（已截断）"
    return text

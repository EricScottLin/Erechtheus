"""Agent 主循环：组装 messages → 调模型 → 执行工具 → 再调模型 → 过滤输出。

与外层插件解耦：输入是「用户文本 + 历史」，输出是已经过
:func:`text.sanitize_output` 处理的最终回复文本。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from nonebot.log import logger

from . import text as text_utils
from .config import Config
from .llm import LLMClient, LLMError, ToolCall, build_image_message, has_images
from .store import ConversationStore
from .tools import Tool, build_tools, execute_tool
from .tools_web import build_search_handler


@dataclass
class AgentReply:
    text: str
    used_tools: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    saw_images: bool = False


class Agent:
    def __init__(self, config: Config, store: ConversationStore) -> None:
        self.config = config
        self.store = store
        self.client = LLMClient(
            api_base=config.qqbot_ai_api_base,
            api_key=config.qqbot_ai_api_key,
            model=config.qqbot_ai_model,
            timeout=config.qqbot_ai_timeout_seconds,
            temperature=config.qqbot_ai_temperature,
            max_tokens=config.qqbot_ai_max_output_tokens,
            extra_body=config.qqbot_ai_extra_body,
            vision_model=config.qqbot_ai_vision_model,
        )
        self._tools: list[Tool] = []
        if config.qqbot_ai_tools_enabled:
            search_fn = build_search_handler(
                config.qqbot_ai_search_backend,
                config.qqbot_ai_search_max_results,
                config.qqbot_ai_search_timeout_seconds,
            )
            self._tools = build_tools(search_fn, config.qqbot_ai_search_max_results)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    def system_prompt(self) -> str:
        prompt = self.config.qqbot_ai_system_prompt
        try:
            return prompt.format(max_chars=self.config.qqbot_ai_max_reply_chars)
        except (KeyError, IndexError, ValueError):
            # 用户自定义 prompt 里有别的花括号（如 JSON 示例），不做格式化
            return prompt

    def build_messages(
        self,
        key: str,
        question: str,
        images: Optional[list[str]] = None,
        user_key: str = "",
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt()}
        ]
        messages.extend(
            self.store.build_context(
                key,
                budget_chars=self.config.qqbot_ai_max_context_chars,
                max_input_chars=self.config.qqbot_ai_max_input_chars,
                user_key=user_key,
            )
        )
        if images:
            messages.append(build_image_message(question, images))
        else:
            messages.append({"role": "user", "content": question})
        return messages

    def screen_question(self, question: str) -> Optional[str]:
        """提问侧预筛：命中 blocked_keywords 时返回固定拒绝语。"""
        hit = text_utils.find_forbidden(question, self.config.qqbot_ai_blocked_keywords)
        if hit:
            logger.info(f"[qqbot_ai] 提问命中拦截词「{hit}」，直接拒答")
            return self.config.qqbot_ai_refusal_reply
        return None

    async def ask(
        self,
        key: str,
        raw_question: str,
        images: Optional[list[str]] = None,
        user_key: str = "",
    ) -> AgentReply:
        """核心入口：压缩输入 → 预筛 → 多轮工具调用 → 过滤输出。

        ``images`` 是已经转好的 base64 data URL 列表（见 :mod:`.vision`）；
        带图时自动切到视觉模型（``qqbot_ai_vision_model``）。

        抛出的异常：:class:`LLMError`（模型调用失败），由外层转成友好提示。
        """
        started = asyncio.get_running_loop().time()
        question = text_utils.compress_input(
            raw_question, limit=self.config.qqbot_ai_max_input_chars
        )
        if not question and not images:
            raise LLMError("空问题")

        refusal = self.screen_question(question)
        if refusal:
            return AgentReply(text=refusal, elapsed=self._elapsed(started))

        messages = self.build_messages(key, question, images, user_key)
        # 带图时用视觉模型；工具也照常可用（视觉模型一般同样支持）
        model = self._pick_model(messages)
        tools_schema = [t.schema() for t in self._tools] or None
        # 看图请求：单独放宽超时，并给思考留出足够的 token
        request_kwargs: dict[str, Any] = {}
        if images:
            request_kwargs["timeout"] = self.config.qqbot_ai_vision_timeout_seconds
            request_kwargs["max_tokens"] = (
                self.config.qqbot_ai_max_output_tokens
                + self.config.qqbot_ai_vision_extra_tokens
            )
        by_name = {t.name: t for t in self._tools}
        used: list[str] = []
        # 工具结果的最后一条（模型死活不肯收尾时，用它拼兜底回答）
        tool_summary = ""

        rounds = 0
        while True:
            result = await self._chat(messages, tools_schema, model, **request_kwargs)

            logger.debug(
                f"[qqbot_ai] 第 {rounds} 轮：calls={[c.name for c in result.tool_calls]}"
            )
            if result.has_tool_calls and rounds < self.config.qqbot_ai_tools_max_rounds:
                rounds += 1
                messages.append(
                    {
                        "role": "assistant",
                        "content": result.content or "",
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": c.raw_arguments or "{}",
                                },
                            }
                            for c in result.tool_calls
                        ],
                    }
                )
                for call in result.tool_calls:
                    content = await self._run_tool(by_name, call)
                    if call.name not in used:
                        used.append(call.name)
                    tool_summary = content
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": content,
                        }
                    )
                continue

            if result.has_tool_calls and not result.content:
                # 达到工具轮数上限且模型仍只想调工具：要求它直接作答
                messages.append(
                    {"role": "user", "content": "请不要再调用工具，直接根据已有信息作答。"}
                )
                final = await self._chat(messages, None, model, **request_kwargs)
                answer = final.content
                # 个别模型在「不要再调工具」后仍会返回 tool_calls + 空正文：
                # 此时正文为空，交给 sanitize 的兜底会变成拒答语，体验很差。
                if not answer and tool_summary:
                    answer = tool_summary
            else:
                answer = result.content
            break

        answer = text_utils.sanitize_output(
            answer,
            max_chars=self.config.qqbot_ai_max_reply_chars,
            forbidden_words=self.config.qqbot_ai_forbidden_words,
            fallback=self.config.qqbot_ai_refusal_reply,
            fold=self.config.qqbot_ai_space_fold,
        )
        # 历史里不存图片（base64 太大），但要把「附图」记下来，方便追问时指代
        history_question = question
        if images:
            note = f"[附图x{len(images)}]"
            history_question = f"{question} {note}".strip() if question else note
        await self.store.add(key, history_question, answer, user_key=user_key)
        return AgentReply(
            text=answer,
            used_tools=used,
            elapsed=self._elapsed(started),
            saw_images=bool(images),
        )

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        model: Optional[str],
        **kwargs: Any,
    ) -> Any:
        """调模型；「思考吃光 max_tokens 导致正文为空」时自动加大额度重试一次。

        带 reasoning 的模型（如 deepseek-v4.1-flash / step-5-preview）在
        max_tokens 偏小时会只有思维链、finish_reason=length，正文为空，
        直接兜底会让用户收到拒答语，体验极差。
        """
        result = await self.client.chat(messages, tools=tools, model=model, **kwargs)
        if result.is_truncated:
            bumped = dict(kwargs)
            base = bumped.get("max_tokens") or self.config.qqbot_ai_max_output_tokens
            bumped["max_tokens"] = min(base * 4, 8192)
            logger.info(
                f"[qqbot_ai] 模型思考占满了 max_tokens（{base}），自动提到 {bumped['max_tokens']} 重试"
            )
            result = await self.client.chat(messages, tools=tools, model=model, **bumped)
        return result

    def _pick_model(self, messages: list[dict[str, Any]]) -> Optional[str]:
        """带图且配置了视觉模型时返回视觉模型名，否则用默认模型。"""
        if has_images(messages) and self.client.vision_model:
            return self.client.vision_model
        return None

    async def _run_tool(self, by_name: dict[str, Tool], call: ToolCall) -> str:
        tool = by_name.get(call.name)
        if tool is None:
            logger.info(f"[qqbot_ai] 模型请求了不存在的工具：{call.name}")
            return f"不存在名为 {call.name} 的工具，请直接作答。"
        logger.info(f"[qqbot_ai] 调用工具 {call.name} args={call.arguments}")
        return await execute_tool(
            tool, call.arguments, self.config.qqbot_ai_tool_result_max_chars
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return round(asyncio.get_running_loop().time() - started, 2)

    async def close(self) -> None:
        await self.client.close()

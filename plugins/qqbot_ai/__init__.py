"""AI 问答插件（qqbot_ai）——喊「小e，xxx」让模型回答，可调用工具查资料。

能力概览：

- **唤醒**：群里 ``小e rule34是什么`` / ``@机器人 rule34是什么``，私聊直接说
- **命令**：``/ai 问 <问题>``、``/ai 重置``、``/ai 状态``、``/ai 自检``
- **工具调用**：模型可自行调用 ``web_search``（ddgs 联网搜索）、``get_current_time``
- **可控输出**：字数上限、违禁词过滤、固定拒答语、违规内容兜底
- **输入压缩**：NFKC 归一、空白折叠、语气词压缩、按字数截断、历史按预算裁剪

配置项见 :mod:`.config`（``qqbot_ai_`` 前缀）。
"""

import asyncio
import re
import time
from pathlib import Path

import nonebot
from nonebot import get_driver, get_plugin_config, on_command, on_message, require
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

require("nonebot_plugin_localstore")

from nonebot_plugin_localstore import get_plugin_data_dir  # noqa: E402

from . import text as text_utils  # noqa: E402
from . import vision  # noqa: E402
from .agent import Agent  # noqa: E402
from .config import Config  # noqa: E402
from .llm import LLMError  # noqa: E402
from .store import ConversationStore  # noqa: E402

try:  # 有 qqbot_group 时接入「/关闭 小e」按群开关
    if (get_plugin := getattr(nonebot, "get_plugin"))("qqbot_group") is not None:
        from . import features  # noqa: E402,F401
except Exception:  # noqa: BLE001 —— 开关是可选能力，失败不影响问答
    pass

__plugin_meta__ = PluginMetadata(
    name="AI 问答",
    description="喊「小e」提问，模型回答；可联网搜索查资料，带字数限制与内容过滤",
    usage="""\
小e <问题>             直接提问（也可 @机器人 <问题>，私聊直接说）
/ai <问题>             命令方式提问
/ai 问 <问题>          同上
/ai 重置               清空本会话上下文
/ai 状态               查看模型、工具、上下文与限额
/ai 自检               测试模型 API 连通性
（群主/群管可用 /关闭 小e 关闭本群）""",
    type="application",
    homepage="https://github.com/nonebot-qqbot",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)

DATA_DIR: Path = get_plugin_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

store = ConversationStore(
    path=DATA_DIR / "history.json",
    max_turns=plugin_config.qqbot_ai_history_turns,
    expire_minutes=plugin_config.qqbot_ai_history_expire_minutes,
    mode=plugin_config.qqbot_ai_history_mode,
)
agent = Agent(plugin_config, store)

# 同一用户的冷却计时：user_id -> 上次提问的 monotonic 时间
_cooldown: dict[str, float] = {}
_notice_at: dict[str, float] = {}
_cooldown_lock = asyncio.Lock()


# ---------------------------------------------------------------- 工具函数
def _wake_regex() -> re.Pattern[str] | None:
    words = [re.escape(w) for w in plugin_config.qqbot_ai_wake_words if w.strip()]
    if not words:
        return None
    # 允许「小e」「小 e」「小E」；唤醒词后跟分隔符或直接接问题
    joined = "|".join(words)
    return re.compile(rf"^(?:{joined})[\s,，:：、.!！~～]*(.+)$", re.IGNORECASE)


_WAKE_RE = _wake_regex()


def session_key(event: MessageEvent) -> str:
    """会话键：群聊按群，私聊按用户（决定上下文存放位置）。"""
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"user:{event.get_user_id()}"


def user_session_key(event: MessageEvent) -> str:
    """提问者键：群聊里也精确到人，用于 ``history_mode=user`` 时不串台。"""
    return f"user:{event.get_user_id()}"


def _command_starts(event: MessageEvent) -> set[str]:
    try:
        return set(get_driver().config.command_start)
    except Exception:  # noqa: BLE001
        return {"/"}


def _clean_text(event: MessageEvent) -> str:
    """取纯文本，并去掉开头的命令前缀（``/`` 等）。"""
    text = event.get_message().extract_plain_text().strip()
    return text_utils.strip_command_prefix(text, _command_starts(event))


def _feature_allowed(event: MessageEvent) -> bool:
    """按群功能开关检查（qqbot_group 未加载时视为开启）。"""
    if not isinstance(event, GroupMessageEvent):
        return True
    try:
        from . import features

        return features.is_enabled(event.group_id)
    except Exception:  # noqa: BLE001
        return True


def _enabled(event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        if not plugin_config.qqbot_ai_group_enabled:
            return False
    elif not plugin_config.qqbot_ai_private_enabled:
        return False
    return _feature_allowed(event)


async def _cooldown_remain(user_id: str) -> float:
    """剩余冷却秒数；0 表示可以提问。可以提问时会刷新计时。"""
    cooldown = plugin_config.qqbot_ai_cooldown_seconds
    if cooldown <= 0:
        return 0.0
    now = time.monotonic()
    async with _cooldown_lock:
        remain = cooldown - (now - _cooldown.get(user_id, 0.0))
        if remain > 0:
            return remain
        _cooldown[user_id] = now
    return 0.0


async def _cooldown_passed(event: MessageEvent, notify: bool = True) -> bool:
    """冷却检查；未通过时（可选）发一句提醒并返回 False。"""
    remain = await _cooldown_remain(event.get_user_id())
    if remain <= 0:
        return True
    user_id = event.get_user_id()
    now = time.monotonic()
    if notify and now - _notice_at.get(user_id, 0.0) > 10:  # 每 10 秒最多提示一次
        _notice_at[user_id] = now
        with_suppress = getattr(event, "bot", None)
        if with_suppress is not None:
            try:
                await with_suppress.send(
                    event, f"慢一点，{int(remain) + 1} 秒后再问吧～"
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[qqbot_ai] 冷却提示发送失败：{e}")
    return False


async def _collect_event_images(event: MessageEvent) -> list[str]:
    """收集本条消息 + 引用（回复）消息里的图片，转成 data URL。"""
    cfg = plugin_config
    if not cfg.qqbot_ai_vision_enabled:
        return []
    limit = cfg.qqbot_ai_vision_max_images
    urls: list[str] = []
    own = event.get_message()
    urls.extend(
        await vision.collect_images(own, limit, cfg.qqbot_ai_vision_max_bytes)
    )
    reply = getattr(event, "reply", None)
    reply_msg = getattr(reply, "message", None) if reply is not None else None
    if reply_msg is not None and len(urls) < limit:
        urls.extend(
            await vision.collect_images(
                reply_msg, limit - len(urls), cfg.qqbot_ai_vision_max_bytes
            )
        )
    return urls[:limit]


def _describe_images(event: MessageEvent) -> str:
    """用户只发图没说话时的默认问题。"""
    if getattr(event, "reply", None) is not None:
        return "这是什么？请描述这张图片的内容。"
    return "帮我看看这张图是什么。"


def _overall_timeout() -> float:
    """整段问答的总超时：每次模型调用 × (工具轮数+2) + 每次搜索耗时。

    带看的时模型调用用视觉超时（慢得多），否则用普通超时。
    """
    cfg = plugin_config
    per_call = max(cfg.qqbot_ai_timeout_seconds, cfg.qqbot_ai_vision_timeout_seconds)
    return per_call * (cfg.qqbot_ai_tools_max_rounds + 2) + cfg.qqbot_ai_tools_max_rounds * (
        cfg.qqbot_ai_search_timeout_seconds + 5
    )


def _answer_fallback(question: str, had_images: bool = False) -> str:
    if had_images and text_utils.wants_search(question):
        return "看图再联网搜索太慢了，这次超时了。直接问问图里写了什么会快很多。"
    if had_images:
        return "这张图我看了太久（视觉模型较慢），再发一次试试？"
    if text_utils.wants_search(question):
        return "这个问题得查资料，但我现在连不上模型/搜索服务，稍后再问问吧。"
    return "小e 现在有点忙（模型服务不可用），稍后再问吧。"


async def _answer(event: MessageEvent, question: str, allow_images: bool = True) -> str:
    """统一提问入口：校验、压缩、调模型、过滤，返回最终文本（不直接发送）。"""
    if not plugin_config.qqbot_ai_api_key:
        return "还没配置模型 API（qqbot_ai_api_key），请找管理员设置。"
    images = await _collect_event_images(event) if allow_images else []
    if text_utils.looks_like_abuse(question) and not images:
        return "想问什么直接说～"
    if not question and images:
        question = _describe_images(event)
    key = session_key(event)
    try:
        reply = await asyncio.wait_for(
            agent.ask(
                key,
                question,
                images=images,
                user_key=user_session_key(event),
            ),
            timeout=_overall_timeout(),
        )
    except asyncio.TimeoutError:
        logger.warning(f"[qqbot_ai] 会话 {key} 整体超时")
        if images:
            return "这张图处理太久了，先跳过。图里写了什么直接问我会更快～"
        return "这次想太久了，先跳过，换个问法试试？"
    except LLMError as e:
        logger.warning(f"[qqbot_ai] 模型调用失败：{e}")
        return _answer_fallback(question, had_images=bool(images))
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[qqbot_ai] 未预期错误：{e}")
        return "出错了，稍后再试。"
    if reply.used_tools:
        logger.info(
            f"[qqbot_ai] {key} 使用工具 {reply.used_tools}，耗时 {reply.elapsed}s"
        )
    return reply.text


# ---------------------------------------------------------------- 唤醒词
async def _wake_rule(event: MessageEvent) -> bool:
    if _WAKE_RE is None or not _enabled(event):
        return False
    text = _clean_text(event)
    m = _WAKE_RE.match(text)
    if not m:
        # 「只发图 + @机器人/引用机器人」也视为唤醒（群里允许 @机器人 图片）
        if not text and vision.image_sources(event.get_message()):
            if not isinstance(event, PrivateMessageEvent) and not event.is_tome():
                return False
            event._qqbot_ai_question = ""  # type: ignore[attr-defined]
            return await _cooldown_passed(event)
        return False
    event._qqbot_ai_question = m.group(1).strip()  # type: ignore[attr-defined]
    return await _cooldown_passed(event)


wake = on_message(rule=Rule(_wake_rule), priority=8, block=True)


@wake.handle()
async def _handle_wake(event: MessageEvent) -> None:
    question = getattr(event, "_qqbot_ai_question", "").strip()
    has_image = bool(vision.image_sources(event.get_message())) or getattr(
        event, "reply", None
    ) is not None
    if not question and not has_image:
        await wake.finish("怎么了？直接说「小e + 你的问题」就行。")
    await wake.finish(await _answer(event, question))


# ---------------------------------------------------------------- @机器人 / 私聊直接问
async def _direct_rule(event: MessageEvent) -> bool:
    if not isinstance(event, PrivateMessageEvent) and not event.is_tome():
        return False
    if not _enabled(event):
        return False
    # 纯图片（无文字）也可以；引用了消息也算有内容
    if not _clean_text(event) and not vision.image_sources(event.get_message()):
        return False
    return await _cooldown_passed(event)


# OneBot 的私聊消息 to_me 恒为 False，所以「@我 或 私聊」用一个规则表达
direct = on_message(rule=Rule(_direct_rule), priority=9, block=True)


@direct.handle()
async def _handle_direct(event: MessageEvent) -> None:
    await direct.finish(await _answer(event, _clean_text(event)))


# ---------------------------------------------------------------- /ai 命令族
ai_cmd = on_command("ai", aliases={"AI"}, priority=5, block=True)


@ai_cmd.handle()
async def _handle_ai(event: MessageEvent, arg: Message = CommandArg()) -> None:
    if not _enabled(event):
        await ai_cmd.finish("本群没开小e，找群管发「/开启 小e」。")

    text = text_utils.fold_whitespace(arg.extract_plain_text())
    head, _, rest = text.partition(" ")

    if head in ("重置", "清空", "reset", "clear"):
        removed = await store.clear(session_key(event))
        store.save()
        await ai_cmd.finish(f"已清空本会话上下文（{removed} 轮）。")

    if head in ("状态", "status"):
        await ai_cmd.finish(_status_text(event))

    if head == "自检":
        await ai_cmd.finish(await _self_check())

    question = rest.strip() if head in ("问", "ask") else text
    if not question:
        await ai_cmd.finish(
            "用法：\n"
            "/ai <问题>      直接问\n"
            "/ai 重置        清空上下文\n"
            "/ai 状态        查看配置\n"
            "群里也可以直接说「小e <问题>」"
        )
    if not await _cooldown_passed(event):
        await ai_cmd.finish()
    # /ai 后面是纯文字参数，没有图片可看，所以不收集图片
    await ai_cmd.finish(await _answer(event, question, allow_images=False))


def _status_text(event: MessageEvent) -> str:
    cfg = plugin_config
    turns = store.turns(session_key(event))
    mode_text = (
        "整群共用（任何人都能接着聊）"
        if store.mode == "shared"
        else "每人独立（只接自己上一问）"
    )
    tools = "、".join(agent.tool_names) or "未启用"
    api = "已配置" if cfg.qqbot_ai_api_key else "未配置（请设 qqbot_ai_api_key）"
    return (
        "🤖 小e 状态\n"
        f"模型：{cfg.qqbot_ai_model}（{cfg.qqbot_ai_api_base}）\n"
        f"API Key：{api}\n"
        f"工具：{tools}\n"
        f"回复上限：{cfg.qqbot_ai_max_reply_chars} 字 / max_tokens={cfg.qqbot_ai_max_output_tokens}\n"
        f"输入上限：{cfg.qqbot_ai_max_input_chars} 字，上下文预算 {cfg.qqbot_ai_max_context_chars} 字\n"
        f"上下文：{mode_text}，{len(turns)} 轮（{cfg.qqbot_ai_history_expire_minutes} 分钟内有效）"
    )


async def _self_check() -> str:
    if not plugin_config.qqbot_ai_api_key:
        return "❌ 未配置 qqbot_ai_api_key"
    try:
        result = await asyncio.wait_for(
            agent.client.chat(
                [{"role": "user", "content": "回复 OK 两个字母"}], max_tokens=16
            ),
            timeout=plugin_config.qqbot_ai_timeout_seconds,
        )
        echo = text_utils.cut_chars(result.content.strip() or "(空)", 40)
        return f"✅ 模型连通，回显：{echo}"
    except Exception as e:  # noqa: BLE001
        return f"❌ 模型不通：{e}"


# ---------------------------------------------------------------- 生命周期
_driver = get_driver()


@_driver.on_startup
async def _startup() -> None:
    if not plugin_config.qqbot_ai_api_key:
        logger.warning("[qqbot_ai] 未配置 qqbot_ai_api_key，喊小e 会提示未配置")
    logger.info(
        f"[qqbot_ai] 就绪：model={plugin_config.qqbot_ai_model} "
        f"tools={agent.tool_names} reply_limit={plugin_config.qqbot_ai_max_reply_chars}"
    )


@_driver.on_shutdown
async def _shutdown() -> None:
    store.save()
    await agent.close()

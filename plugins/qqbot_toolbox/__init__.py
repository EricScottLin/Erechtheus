"""QQBot 工具箱插件。

提供四类基础命令，用于验证消息链路是否打通、以及日常排查：

- ``/ping``    探活，测量接口往返与消息链路延迟（拥堵情况）
- ``/echo``    原样回显，验证「收到 -> 处理 -> 发出」全链路
- ``/help``    列出当前已加载的插件，便于确认插件是否生效
- ``/status``  查询机器人运行状态（在线/收发消息/已运行时长）

遵循 NoneBot 官方插件编写规范：
https://nonebot.dev/docs/tutorial/create-plugin
"""

import time

from nonebot import on_command, get_loaded_plugins, get_plugin_config
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="QQBot 工具箱",
    description="提供 ping / echo / help / status 等基础命令，用于验证消息链路与排查问题",
    usage="""\
/ping            探活，测接口与链路延迟
/echo <内容>      原样回显，验证消息链路
/help            列出已加载的插件
/status          查询在线状态、收发消息数与运行时长""",
    type="application",
    homepage="https://github.com/nonebot-qqbot",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)


# ---------------------------------------------------------------- 工具函数
def _reply_prefix(event: MessageEvent) -> Message:
    """按配置决定是否在回复前面 @ 发起者。"""
    if plugin_config.qqbot_toolbox_at_sender and event.is_tome() is False and event.get_user_id():
        return MessageSegment.at(event.get_user_id()) + MessageSegment.text(" ")
    return Message()


# ---------------------------------------------------------------- /ping
ping = on_command("ping", priority=5, block=True)


def _congestion(ms: float) -> str:
    """按延迟给一句拥堵评价。"""
    if ms < 50:
        return "🚀 畅通无阻，爱琴海无风"
    if ms < 200:
        return "✅ 正常，微澜"
    if ms < 500:
        return "🐌 有点堵，船在排队"
    if ms < 1000:
        return "😰 堵得慌，港口拥挤"
    return "🆘 雅典港大堵船，建议歇会儿"


@ping.handle()
async def handle_ping(bot: Bot, event: MessageEvent) -> None:
    """探活：测量接口往返与消息链路延迟。"""
    prefix = _reply_prefix(event)

    # 接口往返：NoneBot -> LLBot -> NoneBot
    t0 = time.perf_counter()
    try:
        await bot.call_api(
            "get_status", _timeout=plugin_config.qqbot_toolbox_status_timeout
        )
        api_ms = (time.perf_counter() - t0) * 1000
    except Exception:  # noqa: BLE001 —— 探活失败也要给个回应
        api_ms = None

    # 链路延迟：QQ 发出 -> LLBot -> OneBot WS -> NoneBot 收到
    link_ms = (
        (time.time() - event.time) * 1000 if getattr(event, "time", None) else None
    )

    if api_ms is None and link_ms is None:
        await ping.finish(prefix + "🏓 pong——活着，但延迟探不出来，链路可能有问题")

    lines = ["🏓 pong"]
    if link_ms is not None:
        lines.append(f"  链路 {link_ms:.0f}ms（QQ → 我）")
    if api_ms is not None:
        lines.append(f"  接口 {api_ms:.0f}ms（我 → LLBot）")
    worst = max(x for x in (link_ms, api_ms) if x is not None)
    lines.append(f"  {_congestion(worst)}")
    await ping.finish(prefix + "\n".join(lines))


# ---------------------------------------------------------------- /echo
echo = on_command("echo", priority=5, block=True)


@echo.handle()
async def handle_echo(event: MessageEvent, arg: Message = CommandArg()) -> None:
    """原样回显用户输入的内容。"""
    text = arg.extract_plain_text().strip()
    if not text:
        await echo.finish(_reply_prefix(event) + "用法：/echo <内容>，例如 /echo 你好")
    prefix = _reply_prefix(event)
    await echo.finish(prefix + f"收到：{text}")


# ---------------------------------------------------------------- /help
help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)


@help_cmd.handle()
async def handle_help(event: MessageEvent) -> None:
    """列出当前已加载的插件。"""
    plugins = sorted(get_loaded_plugins(), key=lambda p: p.name)
    limit = plugin_config.qqbot_toolbox_help_max

    lines = ["📖 已加载插件："]
    for p in plugins[:limit]:
        lines.append(f"  • {p.name}（matcher ×{len(p.matcher)}）")
    if len(plugins) > limit:
        lines.append(f"  …另有 {len(plugins) - limit} 个插件未显示")

    await help_cmd.finish(_reply_prefix(event) + "\n".join(lines))


# ---------------------------------------------------------------- /status
status_cmd = on_command("status", aliases={"状态"}, priority=5, block=True)


def _duration(seconds: float) -> str:
    """秒数转 '3天4小时25分'。"""
    seconds = int(seconds)
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds}秒"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


@status_cmd.handle()
async def handle_status(bot: Bot, event: MessageEvent) -> None:
    """查询运行状态：在线情况、消息收发统计、已运行时长。"""
    prefix = _reply_prefix(event)
    try:
        info = await bot.call_api(
            "get_status", _timeout=plugin_config.qqbot_toolbox_status_timeout
        )
    except Exception as e:  # noqa: BLE001 —— 状态查询失败不应中断回复
        await status_cmd.finish(prefix + f"⚠️ 状态查询失败：{type(e).__name__}: {e}")

    stat = info.get("stat") or {}
    lines = [
        f"🤖 [{bot.self_id}]",
        f"  在线 {info.get('online')} · 健康 {info.get('good')}",
    ]
    if (recv := stat.get("message_received")) is not None:
        lines.append(f"  收到消息 {recv}")
    if (sent := stat.get("message_sent")) is not None:
        lines.append(f"  发出消息 {sent}")
    if startup := stat.get("startup_time"):
        lines.append(f"  已运行 {_duration(time.time() - startup)}")
    if last := stat.get("last_message_time"):
        lines.append(f"  最后消息 {_duration(time.time() - last)}前")
    await status_cmd.finish(prefix + "\n".join(lines))

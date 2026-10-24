"""舞萌 DX 直连插件 —— 基于 MaimaiDXCHN 的 BotController + ChatBridge。

对用户（全部私聊、全部走白名单）：
  舞萌帮助            指令说明
  舞萌绑定 <二维码>    绑定舞萌账号（二维码来自微信「舞萌DX」小程序）
  舞萌资料            读取玩家资料（真实模式约 1 分钟）
  舞萌成绩            读取最新成绩并生成查分器同步预览
  舞萌预览            用已有缓存重新生成预览
  舞萌上传            确认上传到查分器（需显式开启实传）
  舞萌导出            导出 JSON / CSV
  舞萌状态            绑定、会话与队列状态
  舞萌登出            独立登出并核验远端状态
  舞萌解绑            清空本 QQ 的绑定、缓存与预览

设计约束（与仓库里其他插件一致，出问题先看这里）：
  - 默认 demo 模式：BotController 使用完全离线的演示 SDK，不请求游戏服，也不请求查分器。
    切到 real 必须同时在 .env.prod 改 MAIMAI_DX_MODE 并私聊绑定新二维码。
  - 只处理私聊：二维码、绑定、成绩、导出都属于账号数据，群聊只回一句提示。
  - 只对 bot 主 + 白名单开放：这里用的是自己的机台 keychip，不能让群友随意登录。
  - 真实读取约 1 分钟：阻塞调用全部交给 ChatBridge 的有界线程池，事件循环不阻塞；
    同一账号串行，撞车返回「正在查」，不排队。
  - 二维码不落日志、不落聊天记录：绑定成功后尽力撤回那条消息。
"""

import time
from pathlib import Path

from nonebot import get_driver, get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, PrivateMessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="舞萌DX直连",
    description="直连舞萌官方服：资料/成绩读取、查分器同步预览、导出与登出（默认演示模式）",
    usage="""\
舞萌帮助            看这份说明与当前模式
舞萌绑定 <二维码>   私聊绑定舞萌账号，二维码来自微信「舞萌DX」小程序
舞萌资料            读取玩家资料（真实模式约 1 分钟）
舞萌成绩            读取最新成绩并生成同步预览
舞萌预览            用已有成绩缓存重新生成预览
舞萌上传            确认上传到查分器（需开启实传）
舞萌导出            导出成绩 JSON 与 CSV
舞萌状态            绑定、游戏会话与队列状态
舞萌登出            独立登出并核验远端状态
舞萌解绑            清空本 QQ 的绑定与缓存""",
    type="application",
    homepage="https://github.com/EricScottLin/MaimaiDXCHN",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)

# maimai-dx-chn 以只读挂载 + PYTHONPATH 方式进容器；缺依赖时不让整个 NoneBot 起不来，
# 只在命令里明确报出来（插件加载失败会把 bot.py 直接打挂）。
_IMPORT_ERROR = ""
try:
    from maimai_chn.bot import BotController, BotSettings, UploadAccount
    from maimai_chn.chat import ChatBridge, ChatEvent, ChatSettings
    from maimai_chn.config import MachineConfig
except Exception as exc:  # pragma: no cover - 部署缺依赖时的兜底
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

_READ_TIP = "已收到，正在登录、读取并核验登出，通常约 1 分钟；读完在这里发结果。"


def _allowlist() -> frozenset:
    """bot 主始终可用；白名单从 .env.prod 的 MAIMAI_DX_ALLOW 读（JSON 数组）。"""
    allowed = {str(uid) for uid in get_driver().config.superusers}
    allowed.update(str(uid).strip() for uid in plugin_config.maimai_dx_allow if str(uid).strip())
    return frozenset(allowed)


def _bot_settings():
    default_machine = MachineConfig()
    machine = MachineConfig(
        keychip_id=plugin_config.maimai_dx_keychip_id or default_machine.keychip_id,
        client_id=plugin_config.maimai_dx_client_id or default_machine.client_id,
        region_id=plugin_config.maimai_dx_region_id,
        place_id=plugin_config.maimai_dx_place_id,
    )
    return BotSettings(
        mode=plugin_config.maimai_dx_mode,
        version=plugin_config.maimai_dx_version,
        machine=machine,
        provider=plugin_config.maimai_dx_provider,
        min_session_seconds=plugin_config.maimai_dx_min_session_seconds,
    )


def _chat_settings():
    return ChatSettings(
        private_only=plugin_config.maimai_dx_private_only,
        allowlist=_allowlist(),
        identity_salt=plugin_config.maimai_dx_identity_salt,
        private_reply="这条指令涉及舞萌账号，请私聊我。",
        denied_reply="这台 Bot 的舞萌直连只对主人开放。",
        busy_reply="还有别的舞萌查询在跑（每次约 1 分钟），请稍后再发。",
    )


_BRIDGE = None
_START_ERROR = ""
if not _IMPORT_ERROR:
    try:
        _BRIDGE = ChatBridge(BotController(_bot_settings()), _chat_settings())
    except Exception as exc:  # pragma: no cover - 配置错误时不拖垮整个机器人
        _START_ERROR = f"{type(exc).__name__}: {exc}"
        logger.error(f"舞萌直连初始化失败：{_START_ERROR}")


def _ready_text() -> str:
    return _IMPORT_ERROR or _START_ERROR or "未知原因"


def _export_dir() -> Path:
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        directory = get_plugin_data_dir() / "exports"
    except Exception:  # pragma: no cover - localstore 缺失时退到 cwd
        directory = Path.cwd() / "data" / "qqbot_maimai_dx" / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _attach_upload_account(bridge, chat_event):
    """把环境变量里的查分器凭证挂到当前用户；不通过聊天收集密钥。

    必须在下发指令（尤其是「更新成绩」）之前挂好：BotController 一旦发现凭证变化，
    会清空待确认的同步预览，导致「舞萌上传」无预览可用。
    """
    token = plugin_config.maimai_dx_prober_token.strip()
    if not (plugin_config.maimai_dx_allow_upload and token):
        return
    try:
        bridge.controller.set_upload_account(
            bridge.identity(chat_event), UploadAccount(token=token, allow_upload=True)
        )
    except ValueError:
        # 该用户还有操作在跑，下一条消息再挂；不影响当前指令。
        pass


def _chat_event(event: MessageEvent, text: str) -> ChatEvent:
    return ChatEvent(
        platform="onebot11",
        user_id=str(event.user_id),
        message_id=str(event.message_id),
        text=text,
        private=isinstance(event, PrivateMessageEvent),
    )


async def _send_attachment(matcher, bot: Bot, event: MessageEvent, item) -> None:
    """导出结果先落盘，再尝试私聊发文件；协议端不支持时告诉用户落盘路径。"""
    stamp = int(time.time())
    path = _export_dir() / f"{event.user_id}-{stamp}-{item.name}"
    path.write_text(item.content, encoding="utf-8")
    try:
        await bot.call_api(
            "upload_private_file",
            user_id=int(event.user_id),
            file=str(path),
            name=item.name,
        )
    except Exception as exc:
        logger.warning(f"舞萌导出文件发送失败（{type(exc).__name__}）：{path}")
        await matcher.send(f"文件没发出去，已存在服务器：{path}")


async def _deliver(matcher, bot: Bot, event: MessageEvent, reply) -> None:
    if reply.text:
        await matcher.send(reply.text)
    for item in reply.files:
        await _send_attachment(matcher, bot, event, item)


async def _run(matcher, bot: Bot, event: MessageEvent, text: str) -> None:
    bridge = _BRIDGE
    if bridge is None:
        await matcher.finish("舞萌直连还没就绪：" + _ready_text())
    chat_event = _chat_event(event, text)
    _attach_upload_account(bridge, chat_event)
    reply = await bridge.handle(chat_event)
    if reply is None:
        return  # 重复投递：不重复执行，也不回复
    await _deliver(matcher, bot, event, reply)


def _simple_command(name: str, text: str, announce: str = None):
    matcher = on_command(name, priority=8, block=True)

    @matcher.handle()
    async def _handler(bot: Bot, event: MessageEvent):
        if announce:
            await matcher.send(announce)
        await _run(matcher, bot, event, text)

    return matcher


# ---------------------------------------------------------------- 绑定（二维码）
bind = on_command("舞萌绑定", aliases={"舞萌认证"}, priority=8, block=True)


@bind.handle()
async def _bind(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    qr = arg.extract_plain_text().strip()
    if not qr:
        await bind.finish(
            "用法：私聊发送「舞萌绑定 <二维码内容>」。二维码来自微信「舞萌DX」小程序，"
            "只认证一次，不会登录游戏；绑定成功后我会尽力撤回这条消息。"
        )
    bridge = _BRIDGE
    if bridge is None:
        await bind.finish("舞萌直连还没就绪：" + _ready_text())
    chat_event = _chat_event(event, "绑定 " + qr)
    reply = await bridge.handle(chat_event)
    if reply is None:
        return
    if reply.ok:
        try:
            await bot.delete_msg(message_id=event.message_id)
        except Exception:
            await bind.send("（这条二维码消息建议你自己撤回）")
    await bind.send(reply.text)


# ---------------------------------------------------------------- 帮助
help_matcher = on_command("舞萌帮助", priority=8, block=True)


@help_matcher.handle()
async def _help(event: MessageEvent):
    mode = "演示模式（不请求游戏服）" if plugin_config.maimai_dx_mode == "demo" else "真实模式"
    target = "水鱼" if plugin_config.maimai_dx_provider == "diving-fish" else "落雪"
    upload = "已开启" if plugin_config.maimai_dx_allow_upload else "未开启"
    lines = [
        f"舞萌 DX 直连 · {mode} · 同步目标 {target} · 实传 {upload}",
        "────────────",
        "舞萌绑定 <二维码>   私聊绑定，二维码来自微信「舞萌DX」小程序",
        "舞萌资料            读取玩家资料（真实模式约 1 分钟）",
        "舞萌成绩            读取最新成绩并生成同步预览",
        "舞萌预览            用已有缓存重新生成预览",
        "舞萌上传            确认上传到查分器（需开启实传）",
        "舞萌导出            导出成绩 JSON 与 CSV",
        "舞萌状态            绑定、游戏会话与队列状态",
        "舞萌登出            独立登出并核验远端状态",
        "舞萌解绑            清空本 QQ 的绑定与缓存",
        "────────────",
        "只在这里（私聊）回应；用的是机台凭证，别把二维码发给别人。",
    ]
    if plugin_config.maimai_dx_mode == "demo":
        lines.append("当前是演示模式：所有数据都是本地模拟，发什么都不会真的登录游戏。")
    await help_matcher.finish("\n".join(lines))


# ---------------------------------------------------------------- 状态
status = on_command("舞萌状态", priority=8, block=True)


@status.handle()
async def _status(bot: Bot, event: MessageEvent):
    bridge = _BRIDGE
    if bridge is None:
        await status.finish("舞萌直连还没就绪：" + _ready_text())
    chat_event = _chat_event(event, "状态")
    _attach_upload_account(bridge, chat_event)
    reply = await bridge.handle(chat_event)
    if reply is None:
        return
    stats = bridge.stats()
    summary = (
        f"并发：{stats['active']} 个在查；"
        f"已处理 {stats['handled']}，重复 {stats['duplicates']}，"
        f"拒绝 {stats['rejected']}，忙 {stats['busy']}，失败 {stats['failed']}"
    )
    await status.finish(reply.text + "\n" + summary)


# ---------------------------------------------------------------- 其余指令
_read_announce = _READ_TIP if plugin_config.maimai_dx_mode == "real" else None
_simple_command("舞萌资料", "获取基础信息", announce=_read_announce)
_simple_command("舞萌成绩", "更新成绩", announce=_read_announce)
_simple_command("舞萌预览", "成绩预览")
_simple_command("舞萌上传", "确认上传")
_simple_command("舞萌导出", "导出成绩")
_simple_command("舞萌登出", "登出", announce=_read_announce)
_simple_command("舞萌解绑", "解绑")

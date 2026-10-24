"""舞萌 DX 直连插件 —— 基于 MaimaiDXCHN 的 BotController + ChatBridge。

可见范围（三层权限，任何一层不满足都不出声）：

  1. 群：只有 MAIMAI_DX_GROUP_IDS 里的群可用；其他群完全静默，不回复也不提示。
  2. 私聊：只有「舞萌绑定」可用；其余指令在私聊里静默（不回「去某群用」，
     避免暴露有这个群）。
  3. 绑定：除「舞萌帮助 / 舞萌状态」外都要求先绑定；未绑定统一提示去私聊绑定。

收费见 prices.py：月币结算，先查余额、成功后扣费、失败不收费。
绑定门槛：荣耀 Lv.10（累计 900 荣耀；Lv.9 是觉醒点，必须先 /升阶）。
解绑：仅管理员可用「舞萌解绑 @某人」；满级 Lv.9999 可自助。
菜单隐藏：PluginMetadata.type 设为 "library" —— picmenu-next 与 qqbot_toolbox
都只按这个字段隐藏插件，因此不会出现在 /help、/菜单、/指令 里。
"""

import time
from pathlib import Path

from nonebot import get_driver, get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from . import image, prices, qr
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="舞萌DX直连",
    description="直连舞萌官方服：资料/成绩读取、查分器同步预览、导出与登出",
    usage="""\
舞萌帮助            看这份说明与当前模式（免费）
舞萌状态            绑定、游戏会话与队列状态（免费）
舞萌资料            读取玩家资料（免费）
舞萌成绩 <纹章>      读取最新成绩并生成同步预览
舞萌预览            用已有成绩缓存重新生成预览
舞萌上传            确认上传到查分器（需开启实传）
舞萌导出            导出成绩 JSON 与 CSV
舞萌登出            独立登出并核验远端状态
舞萌绑定            私聊：发送「舞萌绑定」并附二维码图片""",
    # type=library 是刻意的：帮助菜单与 /指令 都只按这个字段隐藏插件。
    # 本插件只在指定群对已授权用户开放，不该出现在公共菜单里。
    type="library",
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
_NEED_BIND = "还没绑定舞萌账号。私聊我发「舞萌绑定」并附上二维码图片即可绑定。"
_FREE_WITHOUT_BIND = {"舞萌帮助", "舞萌状态"}


def _group_ids() -> frozenset[int]:
    return frozenset(int(gid) for gid in plugin_config.maimai_dx_group_ids)


def _in_group(event: MessageEvent) -> bool:
    """只在指定群里响应；其他群 matcher 根本不匹配，一个字都不回。"""
    return isinstance(event, GroupMessageEvent) and event.group_id in _group_ids()


def _in_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


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
    # 群/私聊路由由本插件的 Rule 处理，这里不再让 ChatBridge 拦私聊或白名单。
    return ChatSettings(
        private_only=False,
        allowlist=frozenset(),
        identity_salt=plugin_config.maimai_dx_identity_salt,
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

# 已绑定身份。绑定与成绩缓存都只在进程内存里（容器重启后需重新绑定），
# 和 BotController 的会话状态同生命周期，所以这里也用内存集合。
_BOUND: set[str] = set()


def _ready_text() -> str:
    return _IMPORT_ERROR or _START_ERROR or "未知原因"


def _chat_event(event: MessageEvent, text: str, *, private=None) -> ChatEvent:
    if private is None:
        private = isinstance(event, PrivateMessageEvent)
    return ChatEvent(
        platform="onebot11",
        user_id=str(event.user_id),
        message_id=str(event.message_id),
        text=text,
        private=bool(private),
    )


def _identity(event: MessageEvent) -> str:
    return _BRIDGE.identity(_chat_event(event, ""))


def _is_bound(event: MessageEvent) -> bool:
    return _identity(event) in _BOUND


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
        pass  # 该用户还有操作在跑，下一条消息再挂；不影响当前指令。


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


async def _deliver(matcher, bot: Bot, event: MessageEvent, reply, extra: str = "") -> None:
    text = reply.text
    if extra:
        text = (text + "\n" + extra) if text else extra
    if text.strip():
        await matcher.send(text)
    for item in reply.files:
        await _send_attachment(matcher, bot, event, item)


async def _run_group(matcher, bot: Bot, event: MessageEvent, name: str, text: str, announce=None):
    """群指令统一流程：绑定门槛 → 报价 → 执行 → 成功后扣费 → 交付。"""
    bridge = _BRIDGE
    if bridge is None:
        await matcher.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    if name not in _FREE_WITHOUT_BIND and not _is_bound(event):
        await matcher.finish(_NEED_BIND)
    cost, balance = await prices.quote(uid, name)
    if cost > balance:
        await matcher.finish(f"月币不够：这次要 {cost} 月币，现在只有 {balance}。")
    if announce:
        await matcher.send(announce)
    chat_event = _chat_event(event, text)
    _attach_upload_account(bridge, chat_event)
    reply = await bridge.handle(chat_event)
    if reply is None:
        return  # 重复投递：不重复执行，也不回复
    extra = ""
    if reply.ok and cost > 0:
        charged, cost, after = await prices.charge(uid, name)
        extra = prices.cost_line(cost, after) if charged else f"💸 扣费失败，本次未收费（余额 {after}）"
    await _deliver(matcher, bot, event, reply, extra)


def _simple_command(name: str, text: str, announce: str = None):
    matcher = on_command(name, rule=Rule(_in_group), priority=8, block=True)

    @matcher.handle()
    async def _handler(bot: Bot, event: MessageEvent):
        await _run_group(matcher, bot, event, name, text, announce)

    return matcher


# ---------------------------------------------------------------- 帮助（群内，免费）
help_matcher = on_command("舞萌帮助", rule=Rule(_in_group), priority=8, block=True)


@help_matcher.handle()
async def _help(event: MessageEvent):
    mode = "演示模式（不请求游戏服）" if plugin_config.maimai_dx_mode == "demo" else "真实模式"
    target = "水鱼" if plugin_config.maimai_dx_provider == "diving-fish" else "落雪"
    upload = "已开通" if plugin_config.maimai_dx_allow_upload else "未开通"
    rows = [
        ("舞萌资料", "免费", "读取玩家资料"),
        ("舞萌成绩", None, "读取最新成绩并生成同步预览（约 1 分钟）"),
        ("舞萌预览", None, "用已有成绩缓存重新生成预览"),
        ("舞萌上传", None, f"确认上传到{target}（实传{upload}）"),
        ("舞萌导出", None, "导出成绩 JSON 与 CSV"),
        ("舞萌登出", "免费", "独立登出并核验远端状态"),
        ("舞萌状态", "免费", "绑定、游戏会话与队列状态"),
    ]
    lines = [f"🔧 舞萌直连 · {mode} · 同步目标 {target}", "────────────"]
    for name, tag, note in rows:
        price = prices.price_of(name)
        tag = tag or (f"{price} 月币" if price > 0 else "免费")
        lines.append(f"{name} · {tag} · {note}")
    lines += [
        "────────────",
        f"绑定：私聊发「舞萌绑定」+ 二维码图片；需要荣耀 Lv.{plugin_config.maimai_dx_bind_min_level}",
        "解绑：找管理员发「舞萌解绑 @你」；满级可自助",
        "月币按荣耀等级打折；失败不扣费。",
    ]
    await help_matcher.finish("\n".join(lines))


# ---------------------------------------------------------------- 状态（群内，免费）
status = on_command("舞萌状态", rule=Rule(_in_group), priority=8, block=True)


@status.handle()
async def _status(bot: Bot, event: MessageEvent):
    bridge = _BRIDGE
    if bridge is None:
        await status.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    bound = _is_bound(event)
    if bound:
        reply = await bridge.handle(_chat_event(event, "状态"))
        body = (reply.text + "\n") if reply is not None else ""
    else:
        body = ""  # 未绑定时不重复「账号：未绑定」，统一放到下面的摘要里
    stats = bridge.stats()
    level = int(await prices.core.pricing.level_of(uid))
    balance = int(await prices.core.wallet.balance(uid, prices.CURRENCY))
    summary = (
        f"绑定：{'已绑定' if bound else '未绑定'}\n"
        f"荣耀：Lv.{level}（绑定要求 Lv.{plugin_config.maimai_dx_bind_min_level}）\n"
        f"月币：{balance}\n"
        f"并发：{stats['active']} 个在查；已处理 {stats['handled']}，"
        f"重复 {stats['duplicates']}，忙 {stats['busy']}，失败 {stats['failed']}"
    )
    await status.finish(body + summary)


# ---------------------------------------------------------------- 绑定（私聊）
bind = on_command("舞萌绑定", aliases={"舞萌认证"}, rule=Rule(_in_private), priority=8, block=True)


async def _level_of(uid: str) -> int:
    return int(await prices.core.pricing.level_of(uid))


async def _is_group_member(bot: Bot, uid: str) -> bool:
    """绑定前确认本人在允许的群里；机器人不在群里或查不到都按"不是"处理。"""
    for gid in sorted(_group_ids()):
        try:
            info = await bot.get_group_member_info(group_id=int(gid), user_id=int(uid), no_cache=True)
        except Exception:
            continue
        if info:
            return True
    return False


@bind.handle()
async def _bind(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    bridge = _BRIDGE
    if bridge is None:
        await bind.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    identity = _identity(event)
    if identity in _BOUND:
        await bind.finish("这个 QQ 已经绑定过了。要换号或解绑，请找管理员协助。")
    # 先查群成员，再谈等级：不是本群的人不该知道门槛细节
    if not await _is_group_member(bot, uid):
        await bind.finish("暂时无法为你开通舞萌直连。")
    level = await _level_of(uid)
    if level < plugin_config.maimai_dx_bind_min_level:
        # 等级 = 累计荣耀 // 100 + 1，所以 Lv.N 的门槛是 (N-1)×100 荣耀
        await bind.finish(
            f"还不够绑定：需要荣耀 Lv.{plugin_config.maimai_dx_bind_min_level}（累计 "
            f"{(plugin_config.maimai_dx_bind_min_level - 1) * 100} 荣耀），你现在 Lv.{level}。\n"
            "提示：Lv.9 是第一个觉醒点，要先发 /升阶 才能继续升级。"
        )

    qr_text = arg.extract_plain_text().strip()
    if not qr_text:
        data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
        if not data:
            await bind.finish("请把「舞萌绑定」和二维码图片一起发给我（私聊）。")
        if not qr.AVAILABLE:
            await bind.finish("二维码识别组件不可用：" + qr.unavailable_reason())
        qr_text = qr.decode(data) or ""
        if not qr_text:
            await bind.finish("这张图里没识别到二维码，换一张清晰的原图试试。")

    cost, balance = await prices.quote(uid, "舞萌绑定")
    if cost > balance:
        await bind.finish(f"月币不够：绑定要 {cost} 月币，现在只有 {balance}。")
    reply = await bridge.handle(_chat_event(event, "绑定 " + qr_text, private=True))
    if reply is None:
        return
    if not reply.ok:
        await bind.finish(reply.text)
    _BOUND.add(identity)
    charged, cost, after = await prices.charge(uid, "舞萌绑定")
    try:
        await bot.delete_msg(message_id=event.message_id)  # 尽力撤回二维码原消息
    except Exception:
        await bind.send("（这条二维码消息建议你自己撤回）")
    extra = prices.cost_line(cost, after) if charged else f"💸 扣费失败，本次未收费（余额 {after}）"
    await bind.finish(reply.text + "\n" + extra)


# ---------------------------------------------------------------- 解绑（管理员）
unbind = on_command("舞萌解绑", rule=Rule(_in_group), priority=8, block=True)


def _mentioned(event: MessageEvent, arg: Message) -> str:
    """取要解绑的 QQ：优先 @某人，其次直接写号码。"""
    try:
        for segment in event.get_message():
            if segment.type == "at":
                qq = str(segment.data.get("qq") or "").strip()
                if qq.isdigit():
                    return qq
    except Exception:  # pragma: no cover - 事件结构异常
        pass
    text = arg.extract_plain_text().strip()
    return text if text.isdigit() else ""


@unbind.handle()
async def _unbind(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    bridge = _BRIDGE
    if bridge is None:
        await unbind.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    is_superuser = uid in {str(item) for item in get_driver().config.superusers}
    target = _mentioned(event, arg) or uid
    if target != uid and not is_superuser:
        await unbind.finish("解绑只能由管理员协助（舞萌解绑 @某人）。")
    if target == uid and not is_superuser:
        level = await _level_of(uid)
        if level < plugin_config.maimai_dx_self_unbind_level:
            await unbind.finish(
                f"自助解绑需要满级 Lv.{plugin_config.maimai_dx_self_unbind_level}，"
                "现在还不够，请找管理员协助。"
            )
    target_identity = bridge.identity(ChatEvent(user_id=target, text="解绑"))
    reply = bridge.controller.handle(target_identity, "解绑")
    _BOUND.discard(target_identity)
    suffix = "" if target == uid else f"\n（解绑对象：{target}）"
    await unbind.finish(reply.text + suffix)


# ---------------------------------------------------------------- 其余指令（群内）
_read_announce = _READ_TIP if plugin_config.maimai_dx_mode == "real" else None
_simple_command("舞萌资料", "获取基础信息")
_simple_command("舞萌成绩", "更新成绩", announce=_read_announce)
_simple_command("舞萌预览", "成绩预览")
_simple_command("舞萌上传", "确认上传")
_simple_command("舞萌导出", "导出成绩")
_simple_command("舞萌登出", "登出", announce=_read_announce)

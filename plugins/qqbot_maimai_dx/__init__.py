"""舞萌 DX 直连插件 —— 基于 MaimaiDXCHN 的 BotController + ChatBridge。

可见范围（三层，任何一层不满足都不出声）：

  1. 群：只有 MAIMAI_DX_GROUP_IDS 里的群可用；其他群 matcher 不匹配，完全静默。
  2. 私聊：只有「绑定」可用；其余指令在私聊里静默（不回「去某群用」，避免暴露群）。
  3. 绑定：除「说明 / 我的状态」外都要求先绑定；未绑定统一提示去私聊绑定。

指令名刻意用短词（说明 / 更新 / 上传 / 导出 / 登出 / 绑定 / 解绑 …），但匹配是
**整条消息精确匹配**（允许一个前导 /），所以「更新 了什么」「绑定 手机号」这类
日常闲聊不会误触发；这也避开了 /帮助、/状态、/资料 与其他插件的重名。

收费见 prices.py：月币结算，先查余额、成功后扣费、失败不收费。
绑定门槛：荣耀 Lv.10（累计 900 荣耀；Lv.9 是觉醒点，必须先 /升阶）。
解绑：仅管理员可用「解绑 @某人」；满级 Lv.9999 可自助。
菜单隐藏：PluginMetadata.type 设为 "library" —— picmenu-next 与 qqbot_toolbox
都只按这个字段隐藏插件，因此不会出现在 /help、/菜单、/指令 里。
"""

import asyncio
import re
import time
from pathlib import Path

from nonebot import get_driver, get_plugin_config, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from . import card, image, prices, qr, scorecard
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="舞萌DX直连",
    description="直连舞萌官方服：资料/成绩读取、查分器同步预览、导出与登出",
    usage="""\
说明            看这份说明与当前模式（免费）
大象信息        玩家资料 + 本地状态（免费）
更新成绩        读取最新成绩（约 1 分钟）
导出            导出成绩 JSON 与 CSV
登出            独立登出并核验远端状态
绑定            私聊：发送「绑定」并附二维码图片""",
    # type=library 是刻意的：帮助菜单与 /指令 都只按这个字段隐藏插件。
    # 本插件只在指定群对已绑定用户开放，不该出现在公共菜单里。
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
_NEED_BIND = "还没绑定舞萌账号。私聊我直接发一张舞萌二维码图片就能绑定。"
_FREE_WITHOUT_BIND = {"说明"}

# 指令名 → 传给 BotController 的指令（帮助/状态/解绑单独处理，不在此表）
_GROUP_TEXT = {
    "更新成绩": "更新成绩",
    "导出": "导出成绩",
    "登出": "登出",
}

# 「解绑」允许带 @某人 或直接跟 QQ 号；@ 在纯文本里不占字符，所以两种写法都能覆盖。
_UNBIND_RE = re.compile(r"^[/／]?\s*解绑(?:\s+\d{5,12})?\s*$")


def _group_ids() -> frozenset[int]:
    return frozenset(int(gid) for gid in plugin_config.maimai_dx_group_ids)


def _in_group(event: MessageEvent) -> bool:
    """只在指定群里响应；其他群 matcher 根本不匹配，一个字都不回。"""
    return isinstance(event, GroupMessageEvent) and event.group_id in _group_ids()


def _in_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


def _plain(event: MessageEvent) -> str:
    """消息纯文本（去掉前导 /），用来做「整条消息精确匹配」。

    注意：OneBot v11 的 MessageEvent 没有 get_plain_text()，必须走 get_message()。
    """
    try:
        text = event.get_message().extract_plain_text()
    except Exception:  # pragma: no cover - 事件结构异常
        return ""
    return text.strip().lstrip("/／").strip()


def _group_rule(name: str):
    def checker(event: MessageEvent) -> bool:
        return _in_group(event) and _plain(event) == name

    return Rule(checker)


def _private_rule(name: str):
    def checker(event: MessageEvent) -> bool:
        return _in_private(event) and _plain(event) == name

    return Rule(checker)


def _unbind_rule():
    def checker(event: MessageEvent) -> bool:
        return _in_group(event) and _UNBIND_RE.match(_plain(event)) is not None

    return Rule(checker)


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

    必须在下发指令（尤其是「更新」）之前挂好：BotController 一旦发现凭证变化，
    会清空待确认的同步预览，导致「上传」无预览可用。
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
    if name == "更新成绩" and reply.ok:
        # 上游成功文案在教用户发「确认上传」；该功能已移除，换成我们自己的摘要 + 成绩图
        data = reply.data or {}
        count = data.get("count")
        scores = (data.get("payload") or {}).get("scores") or []
        png = card.render(scores, count=count, simulated=reply.simulated)
        if png:
            await matcher.send(MessageSegment.image(png))
            text = f"共 {count} 条" if count is not None else "已读取最新成绩"
            if reply.simulated:
                text += "（演示数据，未连接游戏）"
        else:
            text = "📥 已读取最新成绩并登出" + (f"，共 {count} 条" if count is not None else "")
        await matcher.finish(text + ("\n" + extra if extra else ""))
    await _deliver(matcher, bot, event, reply, extra)


# ---------------------------------------------------------------- 说明（群内，免费）
help_matcher = on_message(rule=_group_rule("说明"), priority=8, block=True)


@help_matcher.handle()
async def _help(event: MessageEvent):
    mode = "演示模式（不请求游戏服）" if plugin_config.maimai_dx_mode == "demo" else "真实模式"
    rows = [
        ("大象信息", "免费", "玩家资料 + 本地状态（绑定/荣耀/月币/队列）"),
        ("更新成绩", None, "读最新成绩（约 1 分钟）"),
        ("导出", None, "导出成绩 JSON 与 CSV"),
        ("登出", "免费", "独立登出并核验远端状态"),
        ("识图", "免费", "识别成绩结算图（曲名/难度/达成率/判定）并存本地档案"),
    ]
    lines = [f"🔧 舞萌直连 · {mode}", "────────────"]
    for name, tag, note in rows:
        price = prices.price_of(name)
        tag = tag or (f"{price} 月币" if price > 0 else "免费")
        lines.append(f"{name} · {tag} · {note}")
    lines += [
        "────────────",
        f"绑定：私聊直接发二维码图片（自动识别）；需要荣耀 Lv.{plugin_config.maimai_dx_bind_min_level}",
        "解绑：管理员发「解绑 @你」；满级可自助",
        "月币按荣耀等级打折；失败不扣费。",
    ]
    await help_matcher.finish("\n".join(lines))


# ---------------------------------------------------------------- 大象信息（群内，免费）
# 「大象」= 舞萌 DX 的 dx（dx 谐音大象），群里的叫法。
# 一条指令同时给：游戏里的玩家资料（需已绑定）+ 机器人这边的状态（绑定/荣耀/月币/队列）。
info_matcher = on_message(rule=_group_rule("大象信息"), priority=8, block=True)


@info_matcher.handle()
async def _info(bot: Bot, event: MessageEvent):
    bridge = _BRIDGE
    if bridge is None:
        await info_matcher.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    bound = _is_bound(event)
    level = int(await prices.core.pricing.level_of(uid))
    balance = int(await prices.core.wallet.balance(uid, prices.CURRENCY))
    stats = bridge.stats()
    footer = (
        f"──── 机器人状态 ────\n"
        f"绑定：{'已绑定' if bound else '未绑定'}\n"
        f"荣耀：Lv.{level}（绑定要求 Lv.{plugin_config.maimai_dx_bind_min_level}）\n"
        f"月币：{balance}\n"
        f"查询中：{stats['active']} 个"
    )
    if not bound:
        await info_matcher.finish("还没绑定舞萌账号，下面是本地状态。\n私聊我发「绑定」并附上二维码图片即可绑定。\n" + footer)
    cost, remain = await prices.quote(uid, "大象信息")
    if cost > remain:
        await info_matcher.finish(f"月币不够：这次要 {cost} 月币，现在只有 {remain}。")
    reply = await bridge.handle(_chat_event(event, "获取基础信息"))
    if reply is None:
        return
    extra = ""
    if reply.ok and cost > 0:
        charged, cost, after = await prices.charge(uid, "大象信息")
        extra = prices.cost_line(cost, after) if charged else f"💸 扣费失败，本次未收费（余额 {after}）"
    text = (reply.text + "\n" if reply.text else "") + footer
    if extra:
        text += "\n" + extra
    await info_matcher.finish(text)


# ---------------------------------------------------------------- 绑定（私聊）
bind_matcher = on_message(rule=_private_rule("绑定"), priority=8, block=True)


async def _level_of(uid: str) -> int:
    return int(await prices.core.pricing.level_of(uid))


async def _is_group_member(bot: Bot, uid: str) -> bool:
    """绑定前确认本人在允许的群里；机器人不在群里或查不到都按"不是"处理。"""
    for gid in sorted(_group_ids()):
        try:
            info = await bot.get_group_member_info(
                group_id=int(gid), user_id=int(uid), no_cache=True
            )
        except Exception:
            continue
        if info:
            return True
    return False


async def _do_bind(bot: Bot, event: MessageEvent, qr_text: str, matcher) -> None:
    """绑定流程：文字命令与「私聊直接发图」共用。"""
    bridge = _BRIDGE
    if bridge is None:
        await matcher.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    identity = _identity(event)
    if identity in _BOUND:
        await matcher.finish("这个 QQ 已经绑定过了。要换号或解绑，请找管理员协助。")
    # 先查群成员，再谈等级：不是本群的人不该知道门槛细节
    if not await _is_group_member(bot, uid):
        await matcher.finish("暂时无法为你开通舞萌直连。")
    level = await _level_of(uid)
    if level < plugin_config.maimai_dx_bind_min_level:
        # 等级 = 累计荣耀 // 100 + 1，所以 Lv.N 的门槛是 (N-1)×100 荣耀
        await matcher.finish(
            f"还不够绑定：需要荣耀 Lv.{plugin_config.maimai_dx_bind_min_level}（累计 "
            f"{(plugin_config.maimai_dx_bind_min_level - 1) * 100} 荣耀），你现在 Lv.{level}。\n"
            "提示：Lv.9 是第一个觉醒点，要先发 /升阶 才能继续升级。"
        )
    cost, balance = await prices.quote(uid, "绑定")
    if cost > balance:
        await matcher.finish(f"月币不够：绑定要 {cost} 月币，现在只有 {balance}。")
    reply = await bridge.handle(_chat_event(event, "绑定 " + qr_text, private=True))
    if reply is None:
        return
    if not reply.ok:
        # 技术细节只进日志，聊天里只说"失败"，不披露机台/签名这类信息
        logger.warning(f"舞萌绑定失败：{reply.text}")
        await matcher.finish("绑定没成功。重新生成一张二维码再发我；连续失败请联系管理员。")
    _BOUND.add(identity)
    charged, cost, after = await prices.charge(uid, "绑定")
    try:
        await bot.delete_msg(message_id=event.message_id)  # 尽力撤回二维码原消息
    except Exception:
        await matcher.send("（这条二维码消息建议你自己撤回）")
    extra = prices.cost_line(cost, after) if charged else f"扣费失败，本次未收费（余额 {after}）"
    # 单行输出：上游文案里的「获取基础信息」不是我们的指令名，而且换行会被部分客户端
    # 渲染成 <br/>；这里统一用自己的说法。
    tail = f" {extra}" if extra else ""
    await matcher.finish(f"账号绑定成功，现在可以发「大象信息」看资料，或「更新成绩」读成绩。{tail}")


def _private_image_rule(event: MessageEvent) -> bool:
    return _in_private(event) and image.has_image(event)


# 私聊直接发二维码图片就自动绑定（priority 5 抢在 AI 插件前面，图片不进模型）。
qr_image_matcher = on_message(rule=Rule(_private_image_rule), priority=5, block=True)


@qr_image_matcher.handle()
async def _qr_image(bot: Bot, event: MessageEvent):
    if not qr.AVAILABLE:
        await qr_image_matcher.finish("二维码识别组件不可用：" + qr.unavailable_reason())
    data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
    qr_text = qr.decode(data) if data else ""
    if not qr_text:
        await qr_image_matcher.finish(
            "这张图里没识别到二维码。要绑定的话，直接把舞萌小程序里的二维码原图发我。"
        )
    await _do_bind(bot, event, qr_text, qr_image_matcher)


@bind_matcher.handle()
async def _bind(bot: Bot, event: MessageEvent):
    """「绑定」文字命令：保留给"先发文字再补图"的情况；带图的会被上面的自动识别接走。"""
    data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
    if data and qr.AVAILABLE:
        qr_text = qr.decode(data) or ""
        if qr_text:
            await _do_bind(bot, event, qr_text, bind_matcher)
            return
    await bind_matcher.finish("直接私聊发一张舞萌二维码图片就行，我会自动识别并绑定。")


# ---------------------------------------------------------------- 识图（群内，免费）
# 只做「读图 → 结构化 → 存本地」，不写游戏、不写查分器。
score_matcher = on_message(rule=_group_rule("识图"), priority=8, block=True)


@score_matcher.handle()
async def _score(bot: Bot, event: MessageEvent):
    if not scorecard.AVAILABLE:
        await score_matcher.finish("成绩图识别组件不可用：" + scorecard.unavailable_reason())
    data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
    if not data:
        await score_matcher.finish("把成绩结算图和「识图」发在同一条消息里（原图，别截屏压缩）。")
    try:
        items = await asyncio.to_thread(scorecard.ocr, data)
    except Exception:
        await score_matcher.finish("这张图识别失败，换一张正对屏幕的原图试试。")
    result = scorecard.parse(items)
    text = scorecard.summary(result)
    try:
        path = scorecard.archive(str(event.user_id), result)
        text += f"\n📁 已存本地档案：{path.name}"
    except Exception as exc:  # pragma: no cover - 磁盘异常
        logger.warning(f"成绩档案写入失败：{type(exc).__name__}")
    await score_matcher.finish(text)


# ---------------------------------------------------------------- 解绑（管理员）
unbind_matcher = on_message(rule=_unbind_rule(), priority=8, block=True)


def _mentioned(event: MessageEvent) -> str:
    """取要解绑的 QQ：优先 @某人，其次消息末尾直接写号码。"""
    try:
        for segment in event.get_message():
            if segment.type == "at":
                qq = str(segment.data.get("qq") or "").strip()
                if qq.isdigit():
                    return qq
    except Exception:  # pragma: no cover - 事件结构异常
        pass
    match = re.search(r"(\d{5,12})\s*$", _plain(event))
    return match.group(1) if match else ""


@unbind_matcher.handle()
async def _unbind(bot: Bot, event: MessageEvent):
    bridge = _BRIDGE
    if bridge is None:
        await unbind_matcher.finish("舞萌直连还没就绪：" + _ready_text())
    uid = str(event.user_id)
    is_superuser = uid in {str(item) for item in get_driver().config.superusers}
    target = _mentioned(event) or uid
    if target != uid and not is_superuser:
        await unbind_matcher.finish("解绑只能由管理员协助（解绑 @某人）。")
    if target == uid and not is_superuser:
        level = await _level_of(uid)
        if level < plugin_config.maimai_dx_self_unbind_level:
            await unbind_matcher.finish(
                f"自助解绑需要满级 Lv.{plugin_config.maimai_dx_self_unbind_level}，"
                "现在还不够，请找管理员协助。"
            )
    target_identity = bridge.identity(ChatEvent(user_id=target, text="解绑"))
    reply = bridge.controller.handle(target_identity, "解绑")
    _BOUND.discard(target_identity)
    if not reply.ok:
        await unbind_matcher.finish(reply.text)
    suffix = "" if target == uid else f"（解绑对象：{target}）"
    await unbind_matcher.finish("已解绑，绑定与成绩缓存已清空。" + suffix)


# ---------------------------------------------------------------- 其余指令（群内）
_read_announce = _READ_TIP if plugin_config.maimai_dx_mode == "real" else None


def _register(name: str):
    matcher = on_message(rule=_group_rule(name), priority=8, block=True)
    announce = _read_announce if name in ("更新成绩", "登出") else None

    @matcher.handle()
    async def _handler(bot: Bot, event: MessageEvent):
        await _run_group(matcher, bot, event, name, _GROUP_TEXT[name], announce)

    return matcher


for _name in _GROUP_TEXT:
    _register(_name)

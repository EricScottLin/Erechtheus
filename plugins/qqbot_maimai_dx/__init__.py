"""舞萌 DX 直连插件 —— 基于 MaimaiDXCHN 的 BotController + ChatBridge。

可见范围（三层，任何一层不满足都不出声）：

  1. 群：只有 MAIMAI_DX_GROUP_IDS 里的群可用；其他群 matcher 不匹配，完全静默。
  2. 私聊：只有 /绑定、/大象信息 可用；其余指令在私聊里静默（不回「去某群用」，避免暴露群）。
  3. 绑定：除「说明 / 我的状态」外都要求先绑定；未绑定统一提示去私聊绑定。

指令统一带 ``/`` 前缀（``/大象信息``、``/更新成绩``、``/B50`` …），匹配仍是
**整条消息精确匹配**。2026-11-25 起有三条原因：

1. 不带前缀时 /命令冲突：线上 COMMAND_START 含空串，裸「功能列表」这类消息会被
   别的插件的 command matcher 当成参数吃掉（群里实际回成了「没找到功能」）；
2. 群里裸发「大象信息」「更新成绩」和日常聊天分不清，用户也会以为小e 该懂；
3. 解开了一处竞态：小e 的唤醒规则只看消息不以命令前缀开头才接话，而它在
   priority 8，本插件原来也在 8，谁先注册谁说了算、重启后可能互换；带 / 之后
   小e 直接不接，命令永远归本插件。

因为按命令规则匹配，真正的 /绑定 /大象信息 会被写进帮助和纠错命令表（``/大象``
拼错能提示），各指令在这里也登记进 qqbot_help（见文件末尾）。

收费见 prices.py：月币结算，先查余额、成功后扣费、失败不收费。
绑定门槛：荣耀 Lv.10（累计 900 荣耀；Lv.9 是觉醒点，必须先 /升阶）。
解绑：仅管理员可用「解绑 @某人」；满级 Lv.9999 可自助。
菜单隐藏：PluginMetadata.type 设为 "library" —— picmenu-next 与 qqbot_toolbox
都只按这个字段隐藏插件，因此不会出现在 /help、/菜单、/指令 里。
"""

import asyncio
import json
import re
import time
from pathlib import Path

from nonebot import get_driver, get_plugin, get_plugin_config, on_message
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
/说明          看这份说明与当前模式（免费）
/帮助          全部命令总览
/大象信息      玩家资料 + 本地状态（免费）
/更新成绩      读取最新成绩（约 1 分钟），也可发 /B50
/导出          导出成绩 JSON 与 CSV
/登出          独立登出并核验远端状态
/绑定          私聊：发送「/绑定」并附二维码图片""",
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

_READ_TIP = "正在获取玩家数据，通常约 1 分钟。"
_NEED_BIND = "还没绑定舞萌账号，私聊发送登录二维码完成绑定。"
_FREE_WITHOUT_BIND = {"说明"}

# 标准指令 → 别名列表。第一个元素是标准名，其余都是等价写法；
# 别名收到后仍按标准指令执行、计费和回包，用户不需要记住哪一个才是"正式名字"。
# （帮助/状态/解绑单独处理，不在此表）
_GROUP_ALIASES = {
    "更新成绩": ("更新成绩", "更新b50", "更新B50", "B50", "b50"),
    "导出": ("导出", "导出成绩"),
    "登出": ("登出",),
}

# 「解绑」允许带 @某人 或直接跟 QQ 号；@ 在纯文本里不占字符，所以两种写法都能覆盖。
_UNBIND_RE = re.compile(r"^解绑(?:\s+\d{5,12})?\s*$")


def _group_ids() -> frozenset[int]:
    return frozenset(int(gid) for gid in plugin_config.maimai_dx_group_ids)


def _in_group(event: MessageEvent) -> bool:
    """只在指定群里响应；其他群 matcher 根本不匹配，一个字都不回。"""
    return isinstance(event, GroupMessageEvent) and event.group_id in _group_ids()


def _in_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


def _plain(event: MessageEvent) -> str:
    """消息纯文本（保留前导 /），用来做「整条消息精确匹配」。

    注意：OneBot v11 的 MessageEvent 没有 get_plain_text()，必须走 get_message()。
    """
    try:
        text = event.get_message().extract_plain_text()
    except Exception:  # pragma: no cover - 事件结构异常
        return ""
    return text.strip()


def _command_prefix() -> list[str]:
    """配置了的命令前缀，长的在前（``/`` 先于空串）。"""
    try:
        starts = set(get_driver().config.command_start)
    except Exception:  # noqa: BLE001
        starts = {"/"}
    return sorted((s for s in starts if s), key=len, reverse=True)


def _split_command(event: MessageEvent) -> tuple[str, str]:
    """``"/大象信息"`` → ``("/", "大象信息")``；不以任何前缀开头返回 ``("", 文本)``。"""
    text = _plain(event)
    for prefix in _command_prefix():
        if text.startswith(prefix):
            return prefix, text[len(prefix):].strip()
    return "", text


def _is_command(event: MessageEvent, names) -> bool:
    prefix, body = _split_command(event)
    return bool(prefix) and body in names


def _group_rule(names):
    """群内 + 指定群 + 带命令前缀 + 去前缀后整条等于某个名字。"""
    names = (names,) if isinstance(names, str) else tuple(names)

    def checker(event: MessageEvent) -> bool:
        return _in_group(event) and _is_command(event, names)

    return Rule(checker)


def _private_rule(name: str):
    def checker(event: MessageEvent) -> bool:
        return _in_private(event) and _is_command(event, (name,))

    return Rule(checker)


def _anywhere_rule(name: str):
    """私聊和指定群都响应；其余群静默。

    「大象信息」用它：群里的绑定提示是「私聊 /大象信息」，而群里实际使用时
    又是另一种节奏，两边都要能用，否则会出现「明明发了却没反应」。
    """

    def checker(event: MessageEvent) -> bool:
        return (_in_private(event) or _in_group(event)) and _is_command(event, (name,))

    return Rule(checker)


def _unbind_rule():
    def checker(event: MessageEvent) -> bool:
        prefix, body = _split_command(event)
        return _in_group(event) and bool(prefix) and _UNBIND_RE.match(body) is not None

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


# 凭证落盘/恢复的实现要在 _bindings_file 之后，所以放到文件末尾统一装配。
def _bindings_file() -> Path:
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        base = get_plugin_data_dir()
    except Exception:  # pragma: no cover - localstore 缺失时退到 cwd
        base = Path.cwd() / "data" / "qqbot_maimai_dx"
    base.mkdir(parents=True, exist_ok=True)
    return base / "bindings.json"


def _load_bindings() -> dict:
    """identity → 游戏 userId。落盘是为了「一个 QQ 只能绑一个号」跨重启也成立。"""
    try:
        data = json.loads(_bindings_file().read_text(encoding="utf-8"))
    except Exception:
        return {}
    result = {}
    for key, value in (data or {}).items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _save_bindings() -> None:
    try:
        _bindings_file().write_text(json.dumps(_BOUND, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - 磁盘异常
        logger.warning(f"舞萌绑定表写入失败：{type(exc).__name__}")


# identity → 已绑定的游戏 userId。绑定表落盘，成绩缓存只在内存。
_BOUND: dict = _load_bindings()


def _credentials_file() -> Path:
    return _bindings_file().with_name("credentials.json")


def _save_credentials() -> None:
    """把「身份 → userID/token」落盘，重启后不用重新扫码。

    只存认证凭证，不含二维码、Cookie 或游戏会话；文件权限收紧到 0600。
    """
    bridge = _BRIDGE
    if bridge is None:
        return
    path = _credentials_file()
    try:
        data = bridge.controller.export_bindings()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - 某些文件系统不支持
            pass
    except Exception as exc:  # pragma: no cover - 磁盘异常
        logger.warning(f"舞萌凭证写入失败：{type(exc).__name__}")


def _restore_credentials() -> None:
    """启动时把落盘的凭证灌回控制器；坏数据只跳过，不影响启动。"""
    bridge = _BRIDGE
    if bridge is None:
        return
    try:
        data = json.loads(_credentials_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:  # pragma: no cover - 文件损坏
        logger.warning(f"舞萌凭证读取失败：{type(exc).__name__}")
        return
    try:
        count = bridge.controller.restore_bindings(data)
    except Exception as exc:  # pragma: no cover - 数据异常
        logger.warning(f"舞萌凭证恢复失败：{type(exc).__name__}")
        return
    if count:
        logger.info(f"[qqbot_maimai_dx] 已恢复 {count} 个舞萌登录凭证，无需重新扫码")


def _ready_text() -> str:
    """给日志用的失败原因；不外发给用户（技术细节只进日志）。"""
    return _IMPORT_ERROR or _START_ERROR or "未知原因"


_USER_DOWN = "舞萌功能暂时不可用，请稍后再试。"


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


def _has_credentials(event: MessageEvent) -> bool:
    """绑定表有记录且控制器真的持有凭证。

    绑定表落盘、凭证可能缺失（旧版本升级、文件被清），只查绑定表会让
    「正在获取…」和「还没绑定」两条消息一起发出来。
    """
    if _identity(event) not in _BOUND:
        return False
    bridge = _BRIDGE
    if bridge is None:
        return False
    try:
        return bool(bridge.controller.binding(_identity(event)).get("bound"))
    except Exception:  # pragma: no cover - 控制器不可用时按未绑定处理
        return False


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
        await matcher.finish(_USER_DOWN)
    uid = str(event.user_id)
    if name not in _FREE_WITHOUT_BIND and not _has_credentials(event):
        # 绑定表有记录但凭证已丢失（例如旧版本重启）时也走这里：
        # 否则会先发「正在获取」再报「没绑定」，自相矛盾。
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
        ("/大象信息", "免费", "玩家资料 + 本地状态（绑定/荣耀/月币/队列）；私聊也可用"),
        ("/更新成绩", None, "读最新成绩（约 1 分钟），同义：/B50"),
        ("/导出", None, "导出成绩 JSON 与 CSV"),
        ("/登出", "免费", "独立登出并核验远端状态"),
        ("/识图", "免费", "识别成绩结算图（曲名/难度/达成率/判定）并存本地档案"),
    ]
    lines = [f"🔧 舞萌直连 · {mode}", "────────────"]
    for name, tag, note in rows:
        price = prices.price_of(name)
        tag = tag or (f"{price} 月币" if price > 0 else "免费")
        lines.append(f"{name} · {tag} · {note}")
    lines += [
        "────────────",
        f"绑定：私聊发 /绑定 并附二维码图片（自动识别）；需要荣耀 Lv.{plugin_config.maimai_dx_bind_min_level}",
        "解绑：管理员发「/解绑 @你」；满级可自助",
        "注意：全部命令都要带 /；不带 / 的「大象信息」会被小e 当成聊天。",
        "月币按荣耀等级打折；失败不扣费。",
    ]
    await help_matcher.finish("\n".join(lines))


# ---------------------------------------------------------------- 大象信息（私聊+群内，免费）
# 「大象」= 舞萌 DX 的 dx（dx 谐音大象），群里的叫法。
# 一条指令同时给：游戏里的玩家资料（需已绑定）+ 机器人这边的状态（绑定/荣耀/月币/队列）。
# 私聊与指定群都响应：绑定成功那句提示就是让用户「发 /大象信息」，私聊发也该有用。
info_matcher = on_message(rule=_anywhere_rule("大象信息"), priority=8, block=True)


@info_matcher.handle()
async def _info(bot: Bot, event: MessageEvent):
    bridge = _BRIDGE
    if bridge is None:
        await info_matcher.finish(_USER_DOWN)
    uid = str(event.user_id)
    bound = _has_credentials(event)  # 以真实凭证为准，避免显示「已绑定」却查不到
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
        await info_matcher.finish("还没绑定舞萌账号，私聊发送登录二维码完成绑定。\n" + footer)
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
        await matcher.finish(_USER_DOWN)
    uid = str(event.user_id)
    identity = _identity(event)
    known = _BOUND.get(identity)
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
    # 首次绑定收费；同一个号重新登录（换新二维码）不收费
    cost, balance = await prices.quote(uid, "绑定") if known is None else (0, 0)
    if known is None and cost > balance:
        await matcher.finish(f"月币不够：绑定要 {cost} 月币，现在只有 {balance}。")
    reply = await asyncio.to_thread(bridge.controller.bind, identity, qr_text, expect_user_id=known)
    if reply is None:
        return
    if not reply.ok:
        if reply.kind == "account_mismatch":
            await matcher.finish("这个二维码是另一个舞萌号。一个 QQ 只能绑一个号，换号请先让管理员 /解绑。")
        # 二维码过期（一次性用完或超过有效期都算）单独给明确提示，
        # 用户只需要重新获取一张码，不用猜是哪里出问题。
        if reply.kind == "qr_expired":
            await matcher.finish("二维码已过期，重新私聊发一张二维码（直接发图即可，或发 /绑定 带图）。")
        # 其余技术细节只进日志，聊天里只说"失败"，不披露机台/签名这类信息
        logger.warning(f"舞萌绑定失败：{reply.text}")
        await matcher.finish("绑定没成功。重新生成一张二维码再发我；连续失败请联系管理员。")
    info = bridge.controller.binding(identity)
    _BOUND[identity] = int(info.get("user_id") or 0)
    _save_bindings()
    _save_credentials()  # 同步落盘 token，重启后不用重新扫码
    charged, cost, after = await prices.charge(uid, "绑定") if known is None else (True, 0, 0)
    try:
        await bot.delete_msg(message_id=event.message_id)  # 尽力撤回二维码原消息
    except Exception:
        await matcher.send("（这条二维码消息建议你自己撤回）")
    extra = prices.cost_line(cost, after) if charged else f"扣费失败，本次未收费（余额 {after}）"
    # 单行输出：上游文案里的「获取基础信息」不是我们的指令名，而且换行会被部分客户端
    # 渲染成 <br/>；这里统一用自己的说法。
    tail = f" {extra}" if extra else ""
    if known is None:
        # 提示必须写明带 / 的写法：不带 / 的「大象信息」会被小e 当成聊天接走，
        # 用户照着发就收不到资料（2026-11-25 实际发生）。
        await matcher.finish(
            "账号绑定成功，现在可以发 /大象信息 看资料，或 /更新成绩 读成绩"
            "（注意前面要带 /，不带 / 会被当成聊天）。" + tail
        )
    await matcher.finish("已重新登录（还是这个号）。资料发 /大象信息，成绩发 /更新成绩。" + tail)


def _private_image_rule(event: MessageEvent) -> bool:
    return _in_private(event) and image.has_image(event)


# 私聊直接发二维码图片就自动绑定（priority 5 抢在 AI 插件前面，图片不进模型）。
# 解不出二维码时**完全静默**：不认识的图不回、也不进 AI 模型，
# 避免陌生人随便丢张图就能白嫖一次识别/解析。
qr_image_matcher = on_message(rule=Rule(_private_image_rule), priority=5, block=True)


@qr_image_matcher.handle()
async def _qr_image(bot: Bot, event: MessageEvent):
    # 组件不可用或解不出二维码都静默退出；用户主动发「/绑定」才会看到提示。
    if not qr.AVAILABLE:
        return
    data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
    if not data:
        return
    qr_text = qr.decode(data)
    if not qr_text:
        return
    await _do_bind(bot, event, qr_text, qr_image_matcher)


@bind_matcher.handle()
async def _bind(bot: Bot, event: MessageEvent):
    """「/绑定」文字命令：保留给"先发文字再补图"的情况；带图的会被上面的自动识别接走。"""
    data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
    if data and qr.AVAILABLE:
        qr_text = qr.decode(data) or ""
        if qr_text:
            await _do_bind(bot, event, qr_text, bind_matcher)
            return
    await bind_matcher.finish(
        "直接私聊发一张舞萌二维码图片就行，我会自动识别并绑定；也可以发 /绑定 并附上图。"
    )


# ---------------------------------------------------------------- 识图（群内，免费）
# 只做「读图 → 结构化 → 存本地」，不写游戏、不写查分器。
score_matcher = on_message(rule=_group_rule("识图"), priority=8, block=True)


@score_matcher.handle()
async def _score(bot: Bot, event: MessageEvent):
    if not scorecard.AVAILABLE:
        await score_matcher.finish("成绩图识别组件不可用：" + scorecard.unavailable_reason())
    data = await image.first_image_bytes(event, plugin_config.maimai_dx_qr_max_bytes)
    if not data:
        await score_matcher.finish("把成绩结算图和「/识图」发在同一条消息里（原图，别截屏压缩）。")
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
        await unbind_matcher.finish(_USER_DOWN)
    uid = str(event.user_id)
    is_superuser = uid in {str(item) for item in get_driver().config.superusers}
    target = _mentioned(event) or uid
    if target != uid and not is_superuser:
        await unbind_matcher.finish("解绑只能由管理员协助（/解绑 @某人）。")
    if target == uid and not is_superuser:
        level = await _level_of(uid)
        if level < plugin_config.maimai_dx_self_unbind_level:
            await unbind_matcher.finish(
                f"自助解绑需要满级 Lv.{plugin_config.maimai_dx_self_unbind_level}，"
                "现在还不够，请找管理员协助。"
            )
    target_identity = bridge.identity(ChatEvent(user_id=target, text="解绑"))
    reply = bridge.controller.handle(target_identity, "解绑")
    _BOUND.pop(target_identity, None)
    _save_bindings()
    _save_credentials()
    if not reply.ok:
        await unbind_matcher.finish(reply.text)
    suffix = "" if target == uid else f"（解绑对象：{target}）"
    tip = "" if target == uid else f"提醒 TA 重新私聊发二维码绑定后，发 /大象信息 看资料。"
    await unbind_matcher.finish("已解绑，绑定与成绩缓存已清空。" + suffix + (f"\n{tip}" if tip else ""))


# ---------------------------------------------------------------- 其余指令（群内）
_read_announce = _READ_TIP if plugin_config.maimai_dx_mode == "real" else None


def _register(name: str):
    """按标准指令注册，同时让它的所有别名都能触发。"""
    aliases = _GROUP_ALIASES[name]

    def checker(event: MessageEvent) -> bool:
        return _in_group(event) and _is_command(event, aliases)

    matcher = on_message(rule=Rule(checker), priority=8, block=True)
    announce = _read_announce if name in ("更新成绩", "登出") else None

    @matcher.handle()
    async def _handler(bot: Bot, event: MessageEvent):
        # 统一用标准名往下走：扣费、日志、回包都只认一个名字。
        await _run_group(matcher, bot, event, name, name, announce)

    return matcher


for _name in _GROUP_ALIASES:
    _register(_name)


# ---------------------------------------------------------------- 凭证恢复（启动时）
# 放在文件末尾：此时 _BRIDGE、_bindings_file、_restore_credentials 都已定义。
# 没有这一步的话，每次 docker restart 都会让所有人重新扫码——绑定表还在，
# 控制器却已经没有 token 了。
if _BRIDGE is not None:
    _restore_credentials()


# ---------------------------------------------------------------- 帮助登记
# 插件 type=library，不进/菜单 的自动分组；但命令改成了带 / 的真命令之后，
# 写进 qqbot_help 的说明表，/help 和「/大象」拼错的纠错提示才能用上。
try:
    hp = get_plugin("qqbot_help")
    if hp is not None:
        hp.module.describe(
            "说明", desc="舞萌直连说明与收费（/说明）",
            usage=["/说明"], group="舞萌",
        )
        hp.module.describe(
            "大象信息", desc="玩家资料 + 本地状态（免费，群里/私聊都行）",
            usage=["/大象信息"], group="舞萌",
        )
        hp.module.describe(
            "更新成绩", "更新b50", "更新B50", "B50", "b50",
            desc="读取最新成绩并出成绩图（约 1 分钟）",
            usage=["/更新成绩"], group="舞萌",
        )
        hp.module.describe(
            "导出", "导出成绩", desc="导出成绩 JSON 与 CSV", usage=["/导出"], group="舞萌",
        )
        hp.module.describe(
            "登出", desc="独立登出并核验远端状态", usage=["/登出"], group="舞萌",
        )
        hp.module.describe(
            "识图", desc="识别成绩结算图并存本地档案", usage=["/识图（和图发在同一条）"], group="舞萌",
        )
        hp.module.describe(
            "绑定", desc="私聊绑定舞萌账号", usage=["私聊发 /绑定 并附二维码图"], group="舞萌",
        )
        hp.module.describe(
            "解绑", desc="解绑舞萌账号（管理员协助；满级可自助）",
            usage=["/解绑 @某人"], group="舞萌",
        )
except Exception as exc:  # noqa: BLE001 —— 帮助登记是锦上添花，不能拖垮插件
    logger.warning(f"[qqbot_maimai_dx] 帮助登记失败：{type(exc).__name__}: {exc}")

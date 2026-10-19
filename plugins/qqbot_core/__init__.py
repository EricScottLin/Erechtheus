"""QQBot 核心插件 —— 货币与个人信息基础服务。

对用户：
  /余额                查看钱包与荣耀等级
  /名片                查看个人信息卡
  /签名 <内容>          设置签名
  /改昵称 <名字>        设置展示昵称
  /领金币              限时活动，按 QQ 群等级发放月币（bot 主可开关）
  /爆金币 <@某人|QQ> [数量]  仅 bot 主与白名单可直接发币
  /领金币开关 on|off     仅 bot 主私聊可用

对开发者（其他插件）：
  from nonebot.plugin import require
  core = require("qqbot_core")
  await core.wallet.earn(uid, "drachma", 50, day_cap=1000)
  await core.wallet.spend(uid, "drachma", 10)
  await core.wallet.transfer(uid, target, "drachma", 30)
  core.currency.fmt("drachma", 1200)          # '🌙 1,200 月币'
  await core.storage.get_section(uid, "my_plugin", {...})

设计约束：货币与个人信息只落盘在 qqbot_core；玩法插件（qqbot_economy）
通过 require 调用，不直接碰存储文件。
"""

import asyncio

from nonebot import get_driver, get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from . import activities, claim, currency, groups, profile, settings, storage, wallet
from .config import Config
from .currency import CURRENCIES, fmt, get_currency, prestige_level, validate_registry

__plugin_meta__ = PluginMetadata(
    name="QQBot 核心",
    description="货币（月币/荣耀）与个人信息基础服务，供其他插件解耦调用",
    usage="""\
/余额             查看钱包与荣耀等级
/名片             查看个人信息卡
/签名 <内容>       设置签名
/改昵称 <名字>     设置展示昵称
/领金币           限时活动：按群等级领月币
/爆金币 @某人 数量  仅 bot 主/白名单
/领金币开关 on|off  仅 bot 主（群内自动撤回）
/配置           仅 bot 主：查看整体配置（群内自动撤回）""",
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)


# ---------------------------------------------------------------- 工具
def _reply_prefix(event: MessageEvent) -> Message:
    """按配置决定是否在回复前面 @ 发起者。"""
    if plugin_config.qqbot_core_at_sender and not event.is_tome():
        return MessageSegment.at(event.get_user_id()) + MessageSegment.text(" ")
    return Message()


def _is_owner(event: MessageEvent) -> bool:
    return event.get_user_id() in get_driver().config.superusers


def _is_whitelisted(event: MessageEvent) -> bool:
    return event.get_user_id() in plugin_config.qqbot_core_whitelist


def _can_grant(event: MessageEvent) -> bool:
    return _is_owner(event) or _is_whitelisted(event)


def _cap(currency_key: str) -> int | None:
    return plugin_config.qqbot_core_daily_earn_cap.get(currency_key)


# ---------------------------------------------------------------- 管理员临时回复
async def _recall_later(bot: Bot, message_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_msg(message_id=message_id)
    except Exception:  # noqa: BLE001 —— 撤回失败不影响功能
        pass


async def _ephemeral(bot: Bot, matcher, event: MessageEvent, text: str) -> None:
    """群内回复并自动撤回（私聊则正常回复），用于只该 bot 主看到的配置类回复。"""
    resp = await matcher.send(text)
    if isinstance(event, GroupMessageEvent):
        mid = resp.get("message_id") if isinstance(resp, dict) else None
        if mid is not None:
            asyncio.create_task(_recall_later(bot, int(mid), 6.0))
    await matcher.finish()


# ---------------------------------------------------------------- /余额
balance_cmd = on_command("余额", aliases={"钱包", "balance"}, priority=5, block=True)


@balance_cmd.handle()
async def _(event: MessageEvent):
    user = await storage.get_user(event.get_user_id())
    kleos_total = int(user["wallet"]["kleos"]["total_earned"])
    lines = [f"🏛 荣耀等级 Lv.{prestige_level(kleos_total)}（累计 {kleos_total:,}）"]
    for key, spec in CURRENCIES.items():
        lines.append(f"{spec.symbol} {spec.name}：{user['wallet'][key]['balance']:,}")
    await balance_cmd.finish(_reply_prefix(event) + "\n".join(lines))


# ---------------------------------------------------------------- /名片
card_cmd = on_command("名片", aliases={"个人信息", "资料"}, priority=5, block=True)


@card_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    text = await profile.render_card(bot, event, event.get_user_id())
    await card_cmd.finish(_reply_prefix(event) + text)


# ---------------------------------------------------------------- /签名
sign_cmd = on_command("签名", priority=5, block=True)


@sign_cmd.handle()
async def _(event: MessageEvent, arg: Message = CommandArg()):
    text = arg.extract_plain_text().strip()
    if not text:
        await sign_cmd.finish("用法：/签名 <内容>")
    if len(text) > plugin_config.qqbot_core_signature_max:
        await sign_cmd.finish(
            f"签名最多 {plugin_config.qqbot_core_signature_max} 字"
        )
    await profile.update(event.get_user_id(), signature=text)
    await sign_cmd.finish(f"✅ 签名已更新：{text}")


# ---------------------------------------------------------------- /改昵称
nick_cmd = on_command("改昵称", priority=5, block=True)


@nick_cmd.handle()
async def _(event: MessageEvent, arg: Message = CommandArg()):
    name = arg.extract_plain_text().strip()
    if not name:
        await nick_cmd.finish("用法：/改昵称 <名字>")
    if len(name) > plugin_config.qqbot_core_nickname_max:
        await nick_cmd.finish(
            f"昵称最多 {plugin_config.qqbot_core_nickname_max} 字"
        )
    await profile.update(event.get_user_id(), nickname=name)
    await nick_cmd.finish(f"✅ 昵称已更新：{name}")


# ---------------------------------------------------------------- /领金币
claim_cmd = on_command("领金币", aliases={"领取"}, priority=5, block=True)


@claim_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await claim_cmd.finish("请在群里领金币")

    enabled = await settings.get_claim_enabled(
        plugin_config.qqbot_core_claim_enabled
    )
    if not enabled:
        hint = "\n（bot 主可用 /领金币开关 on 开启）" if _is_owner(event) else ""
        await claim_cmd.finish(f"🎐 领金币活动尚未开放（或已结束）{hint}")

    uid = event.get_user_id()

    # 跨群聚合：取 bot 与用户共同所在的群，按最高等级 + 多群小加成发放
    levels = await groups.levels_across_groups(bot, uid)
    if not levels:
        # 降级：只按当前群
        try:
            member = await bot.get_group_member_info(
                group_id=event.group_id, user_id=int(uid)
            )
            levels = {event.group_id: int(member.get("level", 0) or 0)}
        except Exception as e:  # noqa: BLE001 —— 取不到等级按 0 处理
            logger.warning(f"[qqbot_core] get_group_member_info 失败：{e}")
            levels = {event.group_id: 0}

    max_level = max(levels.values())
    group_count = len(levels)
    bonus = min(
        max(0, group_count - 1) * plugin_config.qqbot_core_claim_multi_group_bonus,
        plugin_config.qqbot_core_claim_multi_group_bonus_cap,
    )
    base = claim.amount_for_level(
        max_level,
        plugin_config.qqbot_core_claim_tiers,
        plugin_config.qqbot_core_claim_amount,
    )
    amount = int(round(base * (1 + bonus)))

    result = await claim.claim(
        uid,
        amount,
        {"level": max_level, "groups": group_count, "bonus": round(bonus, 2)},
        _cap("drachma"),
    )
    if result["status"] == "already":
        await claim_cmd.finish(
            f"你已经领过了（{fmt('drachma', result['amount'])}）"
        )
    if result["status"] == "capped":
        await claim_cmd.finish("今日月币获取已达上限，明天再来吧")
    detail = f"最高群等级 {max_level} · {group_count} 个群"
    if bonus > 0:
        detail += f" · 多群加成 +{bonus:.0%}"
    await claim_cmd.finish(
        f"🎉 领取成功：{fmt('drachma', result['amount'])}（{detail}）"
    )


# ---------------------------------------------------------------- /爆金币
grant_cmd = on_command("爆金币", aliases={"发币"}, priority=5, block=True)


def _parse_grant_args(arg: Message) -> tuple[str | None, str]:
    """解析 /爆金币 参数，返回 (目标QQ, 数量文本)。"""
    at = next((seg for seg in arg if seg.type == "at"), None)
    tokens = arg.extract_plain_text().split()
    if at is not None:
        target = str(at.data.get("qq", ""))
        return (target if target.isdigit() else None), (tokens[-1] if tokens else "")
    if tokens and tokens[0].isdigit():
        return tokens[0], (tokens[1] if len(tokens) > 1 else "")
    return None, ""


@grant_cmd.handle()
async def _(event: MessageEvent, arg: Message = CommandArg()):
    if not _can_grant(event):
        await grant_cmd.finish("无权使用该命令")

    target, amount_text = _parse_grant_args(arg)
    if target is None:
        await grant_cmd.finish("用法：/爆金币 <@某人|QQ号> [数量]，默认 100")
    amount = 100
    if amount_text:
        try:
            amount = int(amount_text)
        except ValueError:
            await grant_cmd.finish("数量必须是整数")
    if amount <= 0 or amount > 1_000_000:
        await grant_cmd.finish("数量必须在 1 ~ 1000000 之间")

    await wallet.earn(
        target, "drachma", amount, source=f"grant by {event.get_user_id()}"
    )
    await grant_cmd.finish(
        _reply_prefix(event) + f"✅ 已向 {target} 发放 {fmt('drachma', amount)}"
    )


# ---------------------------------------------------------------- /领金币开关
switch_cmd = on_command(
    "领金币开关", aliases={"活动开关", "领金币活动"}, priority=5, block=True
)


def _parse_bool(text: str) -> bool | None:
    t = text.strip().lower()
    if t in {"on", "开", "开启", "1", "true", "yes"}:
        return True
    if t in {"off", "关", "关闭", "0", "false", "no"}:
        return False
    return None


@switch_cmd.handle()
async def _(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    if not _is_owner(event):
        if isinstance(event, PrivateMessageEvent):
            await switch_cmd.finish("仅 bot 主可用")
        return  # 群里非 bot 主：静默忽略

    text = arg.extract_plain_text().strip()
    if not text:
        enabled = await settings.get_claim_enabled(
            plugin_config.qqbot_core_claim_enabled
        )
        state = "开放中" if enabled else "已关闭"
        await _ephemeral(
            bot, switch_cmd, event,
            f"领金币活动：{state}\n用法：/领金币开关 on|off",
        )
        return

    value = _parse_bool(text)
    if value is None:
        await _ephemeral(
            bot, switch_cmd, event, "用法：/领金币开关 on|off（或 开/关）"
        )
        return
    await settings.set("claim_enabled", value)
    await _ephemeral(
        bot, switch_cmd, event,
        f"✅ 领金币活动已{'开启' if value else '关闭'}",
    )


# ---------------------------------------------------------------- /配置
config_cmd = on_command(
    "配置", aliases={"核心状态", "core状态", "系统配置"}, priority=5, block=True
)


@config_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_owner(event):
        return  # 只给 bot 主看

    enabled = await settings.get_claim_enabled(plugin_config.qqbot_core_claim_enabled)
    override = (await storage.read_settings()).get("claim_enabled", None)
    source = "运行时开关" if override is not None else ".env 默认值"
    tiers = " · ".join(
        f"Lv.{lv}→{amt}" for lv, amt in sorted(plugin_config.qqbot_core_claim_tiers.items())
    )
    caps = " · ".join(
        f"{CURRENCIES[k].name} {v}"
        for k, v in plugin_config.qqbot_core_daily_earn_cap.items()
        if k in CURRENCIES
    )
    users = await storage.all_users()
    drachma = sum(int(u["wallet"]["drachma"]["balance"]) for u in users.values())
    kleos = sum(int(u["wallet"]["kleos"]["total_earned"]) for u in users.values())
    whitelist = "、".join(plugin_config.qqbot_core_whitelist) or "（空）"
    supers = "、".join(sorted(get_driver().config.superusers)) or "（未配置）"

    lines = [
        "⚙️ qqbot_core 当前配置",
        "────────────────",
        f"领金币活动   {'✅ 开放中' if enabled else '⛔ 已关闭'}（{source}）",
        f"群等级档位   {tiers}",
        f"保底发放     {plugin_config.qqbot_core_claim_amount} 月币",
        f"多群加成     每多 1 群 +{plugin_config.qqbot_core_claim_multi_group_bonus:.0%}"
        f"（封顶 +{plugin_config.qqbot_core_claim_multi_group_bonus_cap:.0%}）",
        f"日获取上限   {caps}",
        f"白名单       {whitelist}",
        f"bot 主       {supers}",
        f"货币         {' · '.join(f'{c.symbol} {c.name}' for c in CURRENCIES.values())}",
        "────────────────",
        f"用户数       {len(users)}",
        f"月币流通     {drachma:,} · 荣耀累计 {kleos:,}",
    ]
    await _ephemeral(bot, config_cmd, event, "\n".join(lines))


# ---------------------------------------------------------------- 对玩法插件
async def sign_in_reminders(user_id: str) -> list[str]:
    """供玩法插件在签到成功后调用：返回进行中活动的提醒文案。"""
    return await activities.reminders(
        user_id, plugin_config.qqbot_core_claim_enabled
    )


# ---------------------------------------------------------------- 启动自检
@get_driver().on_startup
async def _self_check() -> None:
    problems = validate_registry()
    for p in problems:
        logger.error(f"[qqbot_core] 货币规范自检失败：{p}")
    if not problems:
        logger.info(
            "[qqbot_core] 货币规范自检通过："
            + " / ".join(f"{c.name}({c.key})" for c in CURRENCIES.values())
        )

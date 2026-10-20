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
from datetime import date

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

from . import (
    activities,
    awaken,
    claim,
    currency,
    groups,
    multipliers,
    pricing,
    profile,
    redpacket,
    settings,
    storage,
    wallet,
)
from .config import Config
from .currency import (
    CURRENCIES,
    MAX_LEVEL,
    cumulative_mileage,
    fmt,
    get_currency,
    kleos_progress,
    level_mileage,
    next_title,
    prestige_level,
    stars_for_level,
    title_for_level,
    validate_registry,
)

__plugin_meta__ = PluginMetadata(
    name="QQBot 核心",
    description="货币（月币/荣耀）与个人信息基础服务，供其他插件解耦调用",
    usage="""\
/余额             查看钱包与荣耀等级
/名片             查看个人信息卡
/称号             查看荣耀称号与折扣
/升阶             达到觉醒点时升阶（★+1，有奖励）
/发红包 金额 份数   仅 bot 主：群里发月币红包
/抢红包           抢群里的红包
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
    status = await awaken.status(event.get_user_id())
    level = status["level"]
    title = status["title"]
    title_txt = f" · {title[2]} {title[1]}" if title else ""
    star_txt = "★" * status["stars"]
    lines = [
        f"🏛 荣耀等级 Lv.{level}{star_txt}（累计 {status['total']:,}）{title_txt}"
    ]
    for key, spec in CURRENCIES.items():
        lines.append(f"{spec.symbol} {spec.name}：{user['wallet'][key]['balance']:,}")
    await balance_cmd.finish(_reply_prefix(event) + "\n".join(lines))


# ---------------------------------------------------------------- /名片
card_cmd = on_command("名片", aliases={"个人信息", "资料"}, priority=5, block=True)


@card_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    text = await profile.render_card(bot, event, event.get_user_id())
    await card_cmd.finish(_reply_prefix(event) + text)


# ---------------------------------------------------------------- /称号
title_cmd = on_command("称号", aliases={"头衔"}, priority=5, block=True)


@title_cmd.handle()
async def _(event: MessageEvent):
    uid = event.get_user_id()
    st = await awaken.status(uid)
    total, level = st["total"], st["level"]
    current, upcoming = st["title"], st["next_title"]
    rate = await pricing.discount(uid)

    into = kleos_progress(total)[1]
    need = level_mileage(level) - into if level < MAX_LEVEL else 0
    head = f"🏅 Lv.{level}"
    if current:
        head += f" · {current[2]} {current[1]}（{'★' * st['stars']}）"
    if st["can_ascend"]:
        head += f"（卡在 Lv.{level}，先升阶）"
    lines = [head, f"   里程：{total:,}（100 荣耀 = 1 级；1000 级后 10 荣耀 = 1 级）"]
    if st["can_ascend"]:
        lines.append("   🌟 可以觉醒了：发 /升阶 领取奖励并继续升级")
    elif level < MAX_LEVEL:
        lines.append(f"   距 Lv.{level + 1} 还差 {need:,} 里程")
    else:
        lines.append("   已满级")
    if upcoming and not st["can_ascend"]:
        need_title = cumulative_mileage(upcoming[0]) - total
        lines.append(
            f"   下一觉醒：{upcoming[2]} {upcoming[1]}（Lv.{upcoming[0]}，"
            f"还需 {max(0, need_title):,} 里程）"
        )
    else:
        lines.append("   已全部觉醒")
    lines.append(f"   荣耀折扣：-{rate:.1%}" if rate else "   荣耀折扣：暂无")
    lines.append("   获取荣耀：每日 /签到 + 捕鱼活跃（日限随等级提升）")
    await title_cmd.finish("\n".join(lines))


# ---------------------------------------------------------------- /发红包
red_cmd = on_command("发红包", priority=5, block=True)


@red_cmd.handle()
async def _(event: MessageEvent, arg: Message = CommandArg()):
    if not _is_owner(event):
        return
    if not isinstance(event, GroupMessageEvent):
        await red_cmd.finish("请在群里发红包")
    parts = arg.extract_plain_text().split()
    if len(parts) < 2:
        await red_cmd.finish("用法：/发红包 <月币总额> <份数> [祝福语]")
    try:
        total, slots = int(parts[0]), int(parts[1])
    except ValueError:
        await red_cmd.finish("总额和份数必须是整数")
    title = parts[2] if len(parts) > 2 else ""
    packet = await redpacket.create(
        event.get_user_id(), event.group_id, total, slots, title
    )
    if packet is None:
        await red_cmd.finish("红包创建失败：月币不足，或总额小于份数")
    await red_cmd.finish(
        f"🧧 红包已发出（{packet.id}）：🌙 {total} 月币 / {slots} 份\n"
        f"群友发 /抢红包 即可抢，30 分钟内有效"
    )


# ---------------------------------------------------------------- /抢红包
grab_cmd = on_command("抢红包", aliases={"领红包"}, priority=5, block=True)


@grab_cmd.handle()
async def _(event: MessageEvent, arg: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await grab_cmd.finish("请在群里抢红包")
    rid = arg.extract_plain_text().strip().upper()
    packet = redpacket.get(rid) if rid else redpacket.latest_in_group(event.group_id)
    if packet is None:
        await grab_cmd.finish("没有可抢的红包")
    status, amount = await redpacket.grab(
        packet.id, event.group_id, event.get_user_id()
    )
    if status == "ok":
        left = f"（还剩 🌙 {packet.remaining} / {packet.slots} 份）" if not packet.done else "（已抢完）"
        await grab_cmd.finish(f"🧧 抢到 🌙 {amount} 月币！{left}")
    elif status == "already":
        await grab_cmd.finish("你已经抢过这个红包了")
    elif status == "wrong_group":
        await grab_cmd.finish("这个红包不在本群")
    else:
        await grab_cmd.finish("红包已抢完或已过期")


# ---------------------------------------------------------------- /倍率
mult_cmd = on_command("倍率", aliases={"加成"}, priority=5, block=True)


@mult_cmd.handle()
async def _(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    if not _is_owner(event):
        return
    text = arg.extract_plain_text().strip()

    if not text:
        info = multipliers.for_user(str(event.get_user_id()))
        name = f"（{info['name']}）" if info["name"] else "（无加成）"
        lines = [f"⚡ 今日荣耀倍率：×{info['mult']:g}{name}"]
        if info.get("dice"):
            d = info["dice"]
            lines.append(
                f"  🖥 {d['label']}：抽到 2^{d['n']} = {d['value']}，{d['fixed']} × {d['value']} = ×{info['mult']:g}"
            )
        if info.get("note"):
            lines.append(f"  💡 {info['note']}")
        ups = multipliers.upcoming()
        if ups:
            lines.append("近期加成：")
            for u in ups:
                span = u["start"] if u["start"] == u["end"] else f"{u['start']}~{u['end'][5:]}"
                lines.append(f"  {span} {multipliers.mult_text(u)} {u['name']}")
        lines.append(
            "/倍率 日历       看节日日历（国庆/除夕 ×20，传统节日 ×10，西方节日 ×5）"
        )
        lines.append(
            "用法：/倍率 周末 3 · /倍率 2026-11-11 5 生日 "
            "· /倍率 区间 起 止 倍率 名称 · /倍率 删除 日期"
        )
        await _ephemeral(bot, mult_cmd, event, "\n".join(lines))
        return

    parts = text.split()
    try:
        if parts[0] in ("日历", "节日"):
            days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
            days = max(1, min(days, 400))
            rows = multipliers.festival_listing(days)
            if not rows:
                msg = f"未来 {days} 天没有节日"
            else:
                head = f"📅 未来 {days} 天节日（共 {len(rows)} 天）："
                shown = rows[:40]
                body = [f"  {d[5:]} {multipliers.mult_text(i)} {i['name']}" for d, i in shown]
                if len(rows) > len(shown):
                    body.append(f"  …还有 {len(rows) - len(shown)} 天")
                msg = "\n".join([head, *body])
        elif parts[0] == "周末" and len(parts) >= 2:
            multipliers.set_weekend(float(parts[1]))
            msg = f"✅ 周末荣耀倍率设为 ×{float(parts[1]):g}"
        elif parts[0] == "删除" and len(parts) >= 2:
            ok = multipliers.remove_date(parts[1])
            msg = f"✅ 已删除 {parts[1]}" if ok else f"没有 {parts[1]} 的规则"
        elif parts[0] == "区间" and len(parts) >= 5:
            mult = float(parts[3])
            multipliers.set_range(parts[1], parts[2], mult, parts[4])
            msg = f"✅ {parts[1]}~{parts[2]} ×{mult:g}（{parts[4]}）"
        elif len(parts) >= 2:
            day = date.fromisoformat(parts[0])
            mult = float(parts[1])
            name = parts[2] if len(parts) > 2 else ""
            uncap = "uncap" in parts
            multipliers.set_date(day.isoformat(), mult, name, uncap=uncap)
            cap_txt = "（捕鱼荣耀不设上限）" if uncap else ""
            msg = f"✅ {day} ×{mult:g} {name}{cap_txt}"
        else:
            msg = "用法：/倍率 周末 3 · /倍率 2026-11-11 5 生日 · /倍率 删除 2026-11-11"
    except Exception as e:  # noqa: BLE001
        msg = f"设置失败：{e}"
    if msg.startswith("📅"):  # 日历是公开信息，不撤回
        await mult_cmd.finish(msg)
    await _ephemeral(bot, mult_cmd, event, msg)


# ---------------------------------------------------------------- /升阶
ascend_cmd = on_command("升阶", aliases={"觉醒"}, priority=5, block=True)


@ascend_cmd.handle()
async def _(event: MessageEvent):
    uid = event.get_user_id()
    st = await awaken.status(uid)
    if not st["can_ascend"]:
        if st["next_threshold"] is not None and st["level"] < st["next_threshold"]:
            await ascend_cmd.finish(
                f"还没到觉醒点：Lv.{st['next_threshold']} 才能升阶（当前 Lv.{st['level']}）"
            )
        await ascend_cmd.finish("已经六星满觉醒啦")
    result = await awaken.ascend(uid)
    if result is None:
        await ascend_cmd.finish("升阶失败，请稍后再试")
    title = result["title"]
    after = await awaken.status(uid)
    nxt = (
        f"下一觉醒点：Lv.{after['next_threshold']}"
        if after["next_threshold"] is not None
        else "已达到最高觉醒"
    )
    lines = [
        f"🌟 觉醒成功！★{result['star']} {title[2]} {title[1]}",
        f"   奖励：{fmt('drachma', result['reward'])}",
        f"   {nxt}",
    ]
    await ascend_cmd.finish("\n".join(lines))


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

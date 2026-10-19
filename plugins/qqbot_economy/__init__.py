"""Erectheus 经济系统 —— 每日签到 + 捕鱼。

双代币设计（详见 docs/CURRENCY_SPEC.md 与 currency.py）：
  荣耀 🏛      每日签到获得，不可消费，累计决定声望等级
  月币 🌙  捕鱼获得，用于鱼饵 / 鱼竿升级 / 抽奖

命令：
  签到            每日签到领荣耀（连签有加成）
  面板            查看个人经济面板
  捕鱼            下网捕鱼，获得月币与经验（有冷却）
  商店            查看可购商品
  购买 <编号>      购买鱼饵或升级鱼竿
  单抽/十连/百连   消耗月币抽奖
  货币            查看货币体系与运行指标（管理员）
"""

import random
import time
from datetime import date, timedelta

from nonebot import get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from . import currency as cur
from .config import Config
from .data import (
    all_users,
    earn,
    get_user,
    sign_in_reminders,
    spend,
    today_str,
    update_user,
)
from .fish import (
    RARITY_COLOR,
    RARITY_LABEL,
    FISHES,
    ROD_RARITY_BOOST_PER_LEVEL,
    pick_fish,
    rarity_probs,
)

__plugin_meta__ = PluginMetadata(
    name="Erectheus 经济",
    description="每日签到获取 荣耀，捕鱼获取 月币，可升级鱼竿与抽奖",
    usage="""\
签到            每日签到领荣耀（连签有加成）
面板            查看个人经济面板
捕鱼            下网捕鱼，获得月币与经验
商店            查看可购商品
购买 <编号>      购买鱼饵或升级鱼竿
单抽/十连/百连   消耗月币抽奖
货币            查看货币体系与运行指标""",
    type="application",
    supported_adapters={"~onebot.v11"},
)

plugin_config = get_plugin_config(Config)

KLEOS = "kleos"
DRACHMA = "drachma"


def _yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _best_value(best_name: str) -> int:
    """按鱼名反查 月币 价值，用于判断是否刷新最佳渔获。"""
    for f in FISHES:
        if f.name == best_name:
            return f.drachma
    return 0


# ---------------------------------------------------------------- /签到
sign = on_command("签到", aliases={"打卡", "每日签到"}, priority=5, block=True)


@sign.handle()
async def handle_sign(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    user = await get_user(uid)
    today = today_str()

    if user["last_sign_in"] == today:
        await sign.finish(f"今天已经签过到了~ 连续 {user['sign_streak']} 天，明天再来吧")

    # 连签判定：昨天签过则 +1，否则重新从 1 开始
    streak = (
        user["sign_streak"] + 1 if user["last_sign_in"] == _yesterday_str() else 1
    )
    kleos = cur.kleos_for_sign_in(streak)

    user = await earn(
        uid,
        KLEOS,
        kleos,
        last_sign_in=today,
        sign_streak=streak,
        total_sign_days=user["total_sign_days"] + 1,
    )

    level = cur.prestige_level(user["total_kleos_earned"])
    streak_note = (
        f"（{cur.SIGN_STREAK_MAX} 天已满，不再增长）"
        if streak >= cur.SIGN_STREAK_MAX
        else ""
    )
    lines = [
        "📜 签到成功",
        f"  获得 {cur.fmt(KLEOS, kleos)}",
        f"  连续签到 {streak} 天{streak_note}",
        f"  累计 {cur.fmt(KLEOS, user['total_kleos_earned'])}",
        f"  声望等级 Lv.{level}",
    ]
    # 进行中的限时活动提醒（由 qqbot_core 判断，失败不影响签到）
    lines += await sign_in_reminders(uid)
    await sign.finish("\n".join(lines))


# ---------------------------------------------------------------- /面板
panel = on_command("面板", aliases={"信息", "我的信息", "profile"}, priority=5, block=True)


@panel.handle()
async def handle_panel(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    u = await get_user(uid)

    level = cur.prestige_level(u["total_kleos_earned"])
    to_next = cur.KLEOS_PER_LEVEL - (u["total_kleos_earned"] % cur.KLEOS_PER_LEVEL)

    lines = [
        f"🏛 {event.sender.card or event.get_user_id()} 的面板",
        "────────────────",
        f"声望等级   Lv.{level}（再 {to_next} 荣耀 升级）",
        f"{cur.fmt(KLEOS, u['kleos'])}   累计 {u['total_kleos_earned']:,}",
        f"签到       连续 {u['sign_streak']} 天 · 共 {u['total_sign_days']} 天",
        "────────────────",
        f"{cur.fmt(DRACHMA, u['drachma'])}",
        f"鱼竿       Lv.{u['rod_level']}/{cur.ROD_MAX_LEVEL}"
        f"（升级需 {cur.rod_upgrade_price(u['rod_level']) or '已满级'}）",
        f"鱼饵       {u['bait']} 个",
        f"渔获       共 {u['total_fish']} 条",
    ]

    if u["fish_count"]:
        parts = [
            f"{RARITY_COLOR[r]} {u['fish_count'][r]}"
            for r in ("R", "SR", "SSR")
            if u["fish_count"].get(r)
        ]
        lines.append("           " + "  ".join(parts))
    if u["best_fish"]:
        lines.append(f"最佳渔获   {u['best_fish']}")

    probs = rarity_probs(u["rod_level"])
    lines.append(
        "────────────────\n当前出货率  "
        + "  ".join(f"{RARITY_COLOR[r]}{r} {p:.1%}" for r, p in probs.items())
    )
    await panel.finish("\n".join(lines))


# ---------------------------------------------------------------- /捕鱼
fish_cmd = on_command("捕鱼", aliases={"钓鱼", "下网"}, priority=5, block=True)


@fish_cmd.handle()
async def handle_fish(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    u = await get_user(uid)

    # 冷却检查（规范 R3：faucet 必须有产能上限）
    now = time.time()
    cd = plugin_config.qqbot_economy_fish_cooldown
    if cd > 0 and now - u["last_fish_ts"] < cd:
        left = int(cd - (now - u["last_fish_ts"]))
        await fish_cmd.finish(f"渔网还在晾晒中，{left} 秒后再来~")

    # 有饵则自动消耗一个，并在回复中说明
    use_bait = u["bait"] > 0
    if use_bait:
        u = await update_user(uid, bait=u["bait"] - 1)

    fish = pick_fish(u["rod_level"], use_bait)
    fc = dict(u["fish_count"])
    fc[fish.rarity] = fc.get(fish.rarity, 0) + 1

    u = await earn(
        uid,
        DRACHMA,
        fish.drachma,
        total_fish=u["total_fish"] + 1,
        fish_count=fc,
        last_fish_ts=now,
        best_fish=(
            fish.name if fish.drachma >= _best_value(u["best_fish"]) else u["best_fish"]
        ),
    )

    lines = [
        "🎣 你一网撒下去——",
        f"{RARITY_COLOR[fish.rarity]} 捕获 {RARITY_LABEL[fish.rarity]} {fish.name}！",
        f"  {fish.detail}",
        f"  卖出换得 {cur.fmt(DRACHMA, fish.drachma)}"
        + (f"，经验 +{fish.exp}" if fish.exp else ""),
    ]
    if use_bait:
        lines.append("  （使用了 1 个鱼饵，稀有度权重提升）")
    await fish_cmd.finish("\n".join(lines))


# ---------------------------------------------------------------- /商店
shop = on_command("商店", aliases={"商铺", "shop"}, priority=5, block=True)


@shop.handle()
async def handle_shop(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    u = await get_user(uid)

    rod_price = cur.rod_upgrade_price(u["rod_level"])
    lines = [
        "⚖️ 雅典港集市",
        "────────────────",
        f"[1] 鱼饵 ×1      {cur.fmt(DRACHMA, plugin_config.qqbot_economy_bait_price)}",
        f"    下次捕鱼稀有度权重 +{plugin_config.qqbot_economy_bait_bonus:.0%}",
    ]
    if rod_price:
        lines += [
            f"[2] 鱼竿升级    {cur.fmt(DRACHMA, rod_price)}",
            f"    Lv.{u['rod_level']} → Lv.{u['rod_level'] + 1}，"
            f"SR/SSR 出货权重 +{ROD_RARITY_BOOST_PER_LEVEL:.0%}",
        ]
    else:
        lines.append("[2] 鱼竿升级    已满级")

    lines += [
        "────────────────",
        "抽奖（高价值，负期望）：",
        f"  单抽 {cur.GACHA_SINGLE} · 十连 {cur.GACHA_TEN} · 百连 {cur.GACHA_HUNDRED} 月币",
        f"你的资产：{cur.fmt(DRACHMA, u['drachma'])}",
        "用法：/购买 1   或   /单抽",
    ]
    await shop.finish("\n".join(lines))


# ---------------------------------------------------------------- /购买
buy = on_command("购买", aliases={"买", "购入"}, priority=5, block=True)


@buy.handle()
async def handle_buy(event: MessageEvent, arg: Message = CommandArg()) -> None:
    uid = str(event.get_user_id())
    item = arg.extract_plain_text().strip()

    if not item:
        await buy.finish("要买什么？/购买 1（鱼饵）或 /购买 2（升级鱼竿）")

    if item == "1":
        price = plugin_config.qqbot_economy_bait_price
        u = await get_user(uid)
        r = await spend(uid, DRACHMA, price, bait=u["bait"] + 1)
        if r is None:
            await buy.finish(f"月币 不足，需要 {cur.fmt(DRACHMA, price)}")
        await buy.finish(f"买到 1 个鱼饵，现有 {r['bait']} 个")

    elif item == "2":
        u = await get_user(uid)
        price = cur.rod_upgrade_price(u["rod_level"])
        if price == 0:
            await buy.finish("鱼竿已满级，去抽奖吧")
        r = await spend(uid, DRACHMA, price, rod_level=u["rod_level"] + 1)
        if r is None:
            await buy.finish(f"月币 不足，升级需要 {cur.fmt(DRACHMA, price)}")
        await buy.finish(f"鱼竿升到 Lv.{r['rod_level']}！稀有度出货权重提升")

    else:
        await buy.finish("没有这个商品，只有 [1] 鱼饵 和 [2] 鱼竿升级")


# ---------------------------------------------------------------- 抽奖
# 负期望设计（规范 §6 缓解措施 #1）：投入 100，期望回收约 57.5 月币
_GACHA_PRIZES = (
    (0.60, DRACHMA, 20, "小补偿"),
    (0.25, DRACHMA, 50, "略有收获"),
    (0.10, "bait", 1, "鱼饵 ×1"),
    (0.04, DRACHMA, 300, "手气不错"),
    (0.01, DRACHMA, 2000, "🎉 海神庇佑！"),
)


def _gacha_expected_value() -> float:
    ev = 0.0
    for p, kind, value, _ in _GACHA_PRIZES:
        ev += p * (value if kind == DRACHMA else plugin_config.qqbot_economy_bait_price)
    return ev


async def _do_gacha(uid: str, times: int, total_cost: int) -> str:
    u = await get_user(uid)
    r = await spend(uid, DRACHMA, total_cost)
    if r is None:
        return f"月币 不足，需要 {cur.fmt(DRACHMA, total_cost)}"
    u = r

    total_back = 0
    got_bait = 0
    best_label = ""
    best_value = 0
    for _ in range(times):
        roll = random.random()
        acc = 0.0
        for p, kind, value, label in _GACHA_PRIZES:
            acc += p
            if roll < acc:
                if kind == DRACHMA:
                    total_back += value
                    if value > best_value:
                        best_value, best_label = value, label
                else:
                    got_bait += value
                break

    u = await earn(uid, DRACHMA, total_back, bait=u["bait"] + got_bait)

    net = total_back + got_bait * plugin_config.qqbot_economy_bait_price - total_cost
    lines = [
        f"🎲 抽奖 {times} 次，花费 {cur.fmt(DRACHMA, total_cost)}",
        f"回收 {cur.fmt(DRACHMA, total_back)}"
        + (f" + 鱼饵 ×{got_bait}" if got_bait else ""),
        f"净{'赚' if net >= 0 else '亏'} {abs(net)} 月币",
    ]
    if best_label:
        lines.append(f"最大奖：{best_label}")
    lines.append(f"当前 {cur.fmt(DRACHMA, u['drachma'])}")
    return "\n".join(lines)


gacha1 = on_command("单抽", priority=5, block=True)
gacha10 = on_command("十连", priority=5, block=True)
gacha100 = on_command("百连", priority=5, block=True)


@gacha1.handle()
async def _h1(event: MessageEvent) -> None:
    await gacha1.finish(await _do_gacha(str(event.get_user_id()), 1, cur.GACHA_SINGLE))


@gacha10.handle()
async def _h10(event: MessageEvent) -> None:
    await gacha10.finish(await _do_gacha(str(event.get_user_id()), 10, cur.GACHA_TEN))


@gacha100.handle()
async def _h100(event: MessageEvent) -> None:
    await gacha100.finish(await _do_gacha(str(event.get_user_id()), 100, cur.GACHA_HUNDRED))


# ---------------------------------------------------------------- /货币（监控）
# 对应规范 §6 的 M_d / S_d / ρ 指标，后期调数值时直接观测。
econ = on_command("货币", aliases={"经济"}, priority=5, block=True, permission=SUPERUSER)


@econ.handle()
async def handle_econ() -> None:
    users = await all_users()
    m = cur.compute_metrics(users)
    ev = _gacha_expected_value()

    lines = ["🏛 Erectheus 货币体系", "────────────────"]
    for spec in cur.CURRENCIES.values():
        lines.append(
            f"{spec.symbol} {spec.name}  L{spec.layer}·"
            f"{'可消费' if spec.consumable else '不可消费'}"
        )
        lines.append(f"   产出：{spec.faucet}")
        lines.append(f"   上限：{spec.faucet_cap}")
        if spec.sinks:
            lines.append(f"   消耗：{' / '.join(spec.sinks)}")

    lines += [
        "────────────────",
        f"活跃用户         {m.active_users}",
        f"荣耀 累计铸造   {m.total_kleos_minted:,}",
        f"月币 累计铸造 {m.total_drachma_minted:,}",
        f"月币 累计销毁 {m.total_drachma_spent:,}",
        f"月币 当前流通 {m.drachma_in_circulation:,}",
    ]
    if m.notes:
        lines.append("────────────────")
        lines.extend(f"⚠️ {n}" for n in m.notes)

    lines += [
        "────────────────",
        f"抽奖期望回收    {ev:.1f} / 投入 {cur.GACHA_SINGLE}"
        f"（负期望 {1 - ev / cur.GACHA_SINGLE:.1%}）",
        f"鱼竿满级总成本  {cur.rod_max_total_cost():,} 月币",
        "完整规范见 docs/CURRENCY_SPEC.md",
    ]
    await econ.finish("\n".join(lines))

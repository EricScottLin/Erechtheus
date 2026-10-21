"""关键词触发的「禁言套餐」系列。

按关键词匹配到不同套餐，给不同的随机时长：

- 精致睡眠（6~12h）、精致午睡（30~90min）、精致下午茶（1~3h）、精致晚餐（1~2h）
- 禁言套餐（1~60min）：牢饭、小黑屋、面壁、坐牢、闭麦、自闭……
- 豪华/至尊/皇帝套餐（2~6h）
- 极致睡眠 / 深度睡眠 / 冬眠（24~72h，来真的）

不指定对象时只禁言发言者自己；只有群主/群管可以 @ / 回复 / QQ 号指定别人。
"""

import random
import re
from dataclasses import dataclass

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)

from . import features
from .config import plugin_config
from .helpers import extract_target, format_duration
from .permissions import check_bot_admin, check_sender_admin, describe_error, finish_denial


@dataclass(frozen=True)
class MutePreset:
    key: str
    name: str
    emoji: str
    min_seconds: int
    max_seconds: int
    keywords: tuple[str, ...]


# 时长单位：秒。sleep / package 会读取配置覆盖，其余用表里的固定区间。
PRESETS: tuple[MutePreset, ...] = (
    MutePreset(
        "sleep", "精致睡眠", "🌙", 6 * 3600, 12 * 3600,
        ("精致睡眠", "精致睡眠套餐", "睡个好觉", "我要睡觉了", "回笼觉"),
    ),
    MutePreset(
        "nap", "精致午睡", "😴", 30 * 60, 90 * 60,
        ("精致午睡", "精致午睡套餐", "午睡", "午休", "打个盹", "眯一会"),
    ),
    MutePreset(
        "tea", "精致下午茶", "🍵", 60 * 60, 3 * 3600,
        ("精致下午茶", "精致下午茶套餐", "下午茶", "喝下午茶"),
    ),
    MutePreset(
        "dinner", "精致晚餐", "🍽️", 60 * 60, 2 * 3600,
        ("精致晚餐", "精致晚餐套餐", "吃顿好的"),
    ),
    MutePreset(
        "package", "禁言套餐", "🎁", 60, 60 * 60,
        (
            "禁言套餐", "禁言大礼包", "闭嘴套餐", "禁言我",
            "牢饭", "吃牢饭", "牢饭套餐", "小黑屋", "关禁闭",
            "面壁思过", "坐牢", "闭麦", "自闭", "反思套餐",
            "沉默套餐", "冷宫", "让我静静", "我想静静",
        ),
    ),
    MutePreset(
        "luxury", "豪华禁言套餐", "💎", 2 * 3600, 6 * 3600,
        ("豪华禁言套餐", "至尊禁言套餐", "皇帝套餐", "豪华套餐"),
    ),
    MutePreset(
        "ultimate", "极致睡眠", "🛌", 24 * 3600, 72 * 3600,
        (
            "极致睡眠", "极致睡眠套餐", "深度睡眠", "深度睡眠套餐",
            "冬眠", "来个大的", "来点大的", "整个大的",
        ),
    ),
)

# 长关键词优先匹配（例如「精致下午茶套餐」优先于「下午茶」）
_KEYWORD_MAP: tuple[tuple[str, MutePreset], ...] = tuple(
    sorted(
        ((kw, preset) for preset in PRESETS for kw in preset.keywords),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


def _normalize(text: str) -> str:
    """去掉空白与首尾标点，便于整句匹配。"""
    t = (text or "").strip()
    t = re.sub(r"\s+", "", t)
    return t.strip("~～!！。.，,")


# 只认「整句就是套餐」或「给我来份 XX」这类明确句式；
# 句子中间顺带提到「坐牢」「自闭」不会触发（避免误判）。
_PREFIX_RE = r"(?:(?:给我来份|给我|来份|来一个|要一份|整一份|安排|请给我|给|帮|个))*"
_SUFFIX_RE = r"(?:套餐)?(?:吧|啦|了|咯|喽|谢谢)*"
_KW_ALTERNATION = "|".join(re.escape(k) for k, _ in _KEYWORD_MAP)
_SELF_MUTE_RE = re.compile(rf"^{_PREFIX_RE}(?:{_KW_ALTERNATION}){_SUFFIX_RE}$")


def match_preset(text: str) -> MutePreset | None:
    normalized = _normalize(text)
    if not normalized or not _SELF_MUTE_RE.match(normalized):
        return None
    for keyword, preset in _KEYWORD_MAP:
        if keyword in normalized:
            return preset
    return None


async def _keyword_rule(event: GroupMessageEvent) -> bool:
    if not features.is_enabled(event.group_id, "selfmute"):
        return False
    return match_preset(event.get_plaintext()) is not None


# 优先级 6：在普通命令（5）之后、普通消息复读（90）之前
selfmute = on_message(rule=_keyword_rule, priority=6, block=True)


def _resolve_target(event: GroupMessageEvent, bot: Bot) -> int | None:
    """@ / 回复 优先；其次允许消息里直接写 QQ 号。"""
    target = extract_target(event, Message(), bot.self_id)
    if target is not None:
        return target
    for token in event.get_plaintext().replace("，", " ").split():
        token = token.strip()
        if token.isdigit() and 5 <= len(token) <= 12:
            return int(token)
    return None


def _duration_seconds(preset: MutePreset) -> int:
    if preset.key == "sleep":
        lo = min(plugin_config.qqbot_group_sleep_min_hours,
                 plugin_config.qqbot_group_sleep_max_hours)
        hi = max(plugin_config.qqbot_group_sleep_min_hours,
                 plugin_config.qqbot_group_sleep_max_hours)
        return random.randint(lo, hi) * 3600
    if preset.key == "ultimate":
        lo = min(plugin_config.qqbot_group_ultimate_min_hours,
                 plugin_config.qqbot_group_ultimate_max_hours)
        hi = max(plugin_config.qqbot_group_ultimate_min_hours,
                 plugin_config.qqbot_group_ultimate_max_hours)
        return random.randint(lo, hi) * 3600
    if preset.key == "package":
        lo = min(plugin_config.qqbot_group_package_min_minutes,
                 plugin_config.qqbot_group_package_max_minutes)
        hi = max(plugin_config.qqbot_group_package_min_minutes,
                 plugin_config.qqbot_group_package_max_minutes)
        return random.randint(lo, hi) * 60
    return random.randint(preset.min_seconds, preset.max_seconds)


@selfmute.handle()
async def handle_selfmute(bot: Bot, event: GroupMessageEvent) -> None:
    preset = match_preset(event.get_plaintext())
    if preset is None:
        return

    sender_id = int(event.get_user_id())
    target = _resolve_target(event, bot)

    # 指定别人：只有群主/群管可以
    if target is not None and target != sender_id:
        denial = check_sender_admin(bot, event)
        if denial:
            await selfmute.finish("只有群主/群管才能指定别人，普通成员只能给自己来一份。")

    # 机器人必须是管理员才能禁言
    denial = await check_bot_admin(bot, event)
    if denial:
        await finish_denial(selfmute, bot, event, denial)

    if target is None:
        target = sender_id

    duration = _duration_seconds(preset)
    try:
        await bot.call_api(
            "set_group_ban",
            group_id=event.group_id,
            user_id=target,
            duration=duration,
        )
    except ActionFailed as e:
        await selfmute.finish(f"禁言失败（{describe_error(e)}）——对方可能是群主或其他管理员。")
    except Exception as e:  # noqa: BLE001
        await selfmute.finish(f"禁言请求发送失败：{type(e).__name__}: {e}")

    extra = ""
    if preset.key == "ultimate":
        extra = "（这可是来真的 🫠）" if target == sender_id else "（下手真狠 🫠）"

    if target == sender_id:
        await selfmute.finish(
            f"{preset.emoji} {preset.name}已生效：{format_duration(duration)} 后见～{extra}"
        )
    await selfmute.finish(
        f"{preset.emoji} 已给 {MessageSegment.at(target)} 安排{preset.name}"
        f"：{format_duration(duration)}{extra}"
    )

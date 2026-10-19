"""个人信息：数据读写 + 资料卡渲染。

平台信息（群名片/头像/性别）来自 nonebot-plugin-userinfo（若已安装），
自设字段（昵称/签名/入坑日期）存在 qqbot_core 的 profile 段。
"""

from typing import Any

from . import storage, wallet
from .currency import fmt, prestige_level, stars_for_level, title_for_level

try:  # 可选依赖：装了 userinfo 才能取平台信息
    from nonebot_plugin_userinfo import get_user_info as _get_platform_info
except Exception:  # pragma: no cover
    _get_platform_info = None


async def get(user_id: str) -> dict[str, Any]:
    return await storage.get_profile(user_id)


async def update(
    user_id: str, nickname: str | None = None, signature: str | None = None
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if nickname is not None:
        fields["nickname"] = nickname
    if signature is not None:
        fields["signature"] = signature
    return await storage.update_profile(user_id, **fields)


async def platform_info(bot, event, user_id: str) -> dict[str, Any]:
    """取平台侧信息；userinfo 未安装或无对应适配器时返回空 dict。"""
    if _get_platform_info is None:
        return {}
    try:
        info = await _get_platform_info(bot, event, user_id)
    except Exception:  # noqa: BLE001 —— 平台查询失败不应影响资料卡
        return {}
    if info is None:
        return {}
    avatar = ""
    if info.user_avatar is not None:
        try:
            avatar = info.user_avatar.get_url()
        except Exception:  # noqa: BLE001
            avatar = ""
    return {
        "user_name": info.user_name or "",
        "display_name": info.user_displayname or "",
        "gender": str(info.user_gender),
        "avatar": avatar,
    }


async def render_card(bot, event, user_id: str) -> str:
    """渲染资料卡。"""
    profile = await storage.get_profile(user_id)
    balances = await wallet.balances(user_id)
    total_kleos = 0
    user = await storage.get_user(user_id)
    total_kleos = int(user["wallet"]["kleos"]["total_earned"])
    level = prestige_level(total_kleos)

    platform = await platform_info(bot, event, user_id)
    display = profile.get("nickname") or platform.get("display_name") or platform.get(
        "user_name"
    ) or "未设置昵称"

    lines = [f"🪪 {display}（{user_id}）"]
    if platform.get("user_name"):
        lines.append(f"  平台昵称：{platform['user_name']}")
    lines.append(f"  签名：{profile.get('signature') or '（空）'}")
    lines.append(f"  入坑：{profile.get('joined_at') or '—'}")
    title = title_for_level(level)
    title_txt = f" · {title[2]} {title[1]}" if title else ""
    star_txt = "★" * stars_for_level(level)
    lines.append(f"  荣耀 Lv.{level}{star_txt}（累计 {total_kleos:,}）{title_txt}")
    wallet_lines = " · ".join(
        fmt(key, value) for key, value in balances.items() if value
    )
    lines.append(f"  钱包：{wallet_lines or '（空）'}")
    return "\n".join(lines)

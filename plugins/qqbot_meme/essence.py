"""群精华自动收录。

OneBot v11 没有「精华变更」通知事件，只能定时轮询：

1. ``get_group_list`` 拿机器人所在的群
2. ``get_essence_msg_list`` 拿每群精华元数据（只有 message_id / 发送者等，没有正文）
3. ``get_msg(message_id)`` 取正文
4. 没记录过的精华消息，把纯文本追加进 ``quotes.json``

已处理的 message_id 存在 ``essence_seen.json``，避免重复收录。
"""

from nonebot import get_bots, require
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.log import logger

from . import library
from .config import plugin_config

try:  # apscheduler 缺失时降级为「只支持 /金句同步 手动触发」
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler  # noqa: E402
except Exception:  # noqa: BLE001
    scheduler = None


def extract_text(msg: object) -> str:
    """从 ``get_msg`` 的返回里提取纯文本。"""
    if not isinstance(msg, dict):
        return ""
    segments = msg.get("message")
    if isinstance(segments, str):
        # 兼容 CQ 码字符串
        try:
            return Message(segments).extract_plain_text().strip()
        except Exception:  # noqa: BLE001
            return segments.strip()
    if isinstance(segments, list):
        parts: list[str] = []
        for item in segments:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str((item.get("data") or {}).get("text") or ""))
        return "".join(parts).strip()
    return ""


async def sync_once(bot: Bot) -> tuple[int, int]:
    """同步一次，返回 ``(检查群数, 新增金句数)``。"""
    seen = library.load_essence_seen()
    added = 0

    groups = await bot.get_group_list()
    groups = [
        g for g in (groups or []) if isinstance(g, dict) and g.get("group_id")
    ]

    for group in groups:
        group_id = int(group["group_id"])
        existing = set(library.load_quotes(group_id))
        try:
            items = await bot.get_essence_msg_list(group_id=group_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_meme] 获取群 {group_id} 精华列表失败：{e}")
            continue

        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                message_id = int(item["message_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if message_id in seen:
                continue
            seen.add(message_id)

            try:
                msg = await bot.get_msg(message_id=message_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_meme] 获取精华消息 {message_id} 失败：{e}")
                continue

            text = extract_text(msg)
            if not text or text in existing:
                continue

            author = str(item.get("sender_nick") or "").strip() or None
            group_name = msg.get("group_name") if isinstance(msg, dict) else None
            await library.append_quote(
                text, author=author, group_id=group_id, group_name=group_name
            )
            existing.add(text)
            added += 1

    library.save_essence_seen(seen)
    return len(groups), added


def _interval_seconds() -> int:
    return max(30, int(plugin_config.qqbot_meme_essence_interval))


if scheduler is not None:

    @scheduler.scheduled_job(
        "interval",
        seconds=_interval_seconds(),
        id="qqbot_meme_essence_sync",
        max_instances=1,
    )
    async def _periodic_sync() -> None:
        """定时把新精华收进金句库。"""
        if not plugin_config.qqbot_meme_essence_sync:
            return
        for bot in get_bots().values():
            if bot.adapter.get_name() != "OneBot V11":
                continue
            try:
                groups, added = await sync_once(bot)  # type: ignore[arg-type]
                if added:
                    logger.info(
                        f"[qqbot_meme] 精华同步：检查 {groups} 个群，新增金句 {added} 条"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[qqbot_meme] 精华同步失败：{type(e).__name__}: {e}")

"""群管权限闸门与「机器人最近发送消息」记录。

- 权限：发送者必须是群主 / 群管 / 机器人超级用户 / 配置的额外管理员；
  禁言、踢人、全体禁言还要求机器人自身是本群管理员。
- 记录：用 NoneBot 的 ``call_api`` 后置钩子把每次 ``send_msg`` 返回的
  message_id 按群记下来，供 ``/撤`` 使用。
"""

from nonebot.adapters.onebot.v11 import ActionFailed, Bot, GroupMessageEvent, MessageSegment

from .config import plugin_config
from .helpers import is_extra_admin, is_group_admin, is_superuser


def describe_error(exc: Exception) -> str:
    """把 OneBot 的 ActionFailed / 其他异常转成可读文案。"""
    if isinstance(exc, ActionFailed):
        info = getattr(exc, "info", {}) or {}
        msg = info.get("wording") or info.get("msg") or info.get("message")
        retcode = info.get("retcode")
        if msg and retcode is not None:
            return f"{msg}（retcode={retcode}）"
        if msg:
            return str(msg)
        if retcode is not None:
            return f"接口返回失败（retcode={retcode}）"
        return "接口返回失败"
    return f"{type(exc).__name__}: {exc}"


def check_sender_admin(bot: Bot, event: GroupMessageEvent) -> str | None:
    """仅校验发送者是否有群管权限（不检查机器人自身角色）。

    撤回机器人自己的消息不需要机器人是管理员，所以 ``/撤`` 只走这一层。
    """
    if not (
        is_group_admin(event)
        or is_superuser(event, bot.config.superusers)
        or is_extra_admin(event, plugin_config.qqbot_group_extra_admins)
    ):
        return "只有群主、群管或机器人管理员可以使用群管命令哦。"
    return None


async def check_bot_admin(bot: Bot, event: GroupMessageEvent) -> str | None:
    """仅校验机器人自身是否是本群管理员。"""
    try:
        info = await bot.call_api(
            "get_group_member_info",
            group_id=event.group_id,
            user_id=int(bot.self_id),
        )
    except ActionFailed as e:
        return f"查询我在本群的权限失败（{describe_error(e)}），暂时无法执行群管操作。"
    except Exception as e:  # noqa: BLE001 —— 网络错误等也不应让命令裸奔
        return f"查询我在本群的权限失败（{type(e).__name__}: {e}），暂时无法执行群管操作。"

    role = info.get("role") if isinstance(info, dict) else None
    if role not in ("admin", "owner"):
        return "我在本群还不是管理员，没有禁言/移出权限，请让群主给我一个管理员。"
    return None


async def check_admin(bot: Bot, event: GroupMessageEvent) -> str | None:
    """统一权限闸门：发送者权限 + 机器人自身管理员权限。

    返回 ``None`` 表示放行；否则返回应当回复给用户的拒绝原因。
    """
    denial = check_sender_admin(bot, event)
    if denial:
        return denial
    return await check_bot_admin(bot, event)


async def finish_denial(matcher, bot: Bot, event: GroupMessageEvent, reason: str) -> None:
    """带 @ 的拒绝回复。"""
    await matcher.finish(MessageSegment.at(event.get_user_id()) + " " + reason)


# ---------------------------------------------------------------- 发送记录
# /撤 需要知道「机器人上一条消息」的 message_id。OneBot 的 send_msg 返回值里带
# message_id，这里按群记录下来。注意：只记录本进程启动之后、且带 group_id 的发送。
_last_sent: dict[int, int] = {}


async def _record_last_sent(
    bot: Bot, exc: Exception | None, api: str, data: dict, result: object
) -> None:
    if exc is not None or api not in ("send_msg", "send_group_msg"):
        return
    group_id = data.get("group_id")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if group_id is None or message_id is None:
        return
    try:
        _last_sent[int(group_id)] = int(message_id)
    except (TypeError, ValueError):
        pass


def _register_recorder() -> None:
    """注册记录钩子；插件被重复加载时不重复添加。"""
    if _record_last_sent not in Bot._called_api_hook:
        Bot._called_api_hook.add(_record_last_sent)


_register_recorder()

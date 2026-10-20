"""通用触发规则。"""

from nonebot import get_driver
from nonebot.rule import Rule


def explicit_prefix_or_at() -> Rule:
    """只允许「显式命令前缀」或「@机器人」触发。

    本项目 COMMAND_START 含 ""，裸词在群聊里容易误触发；``@机器人 xxx``
    会被 OneBot 适配器剥掉 @ 段并置 ``to_me``，仍然放行。
    """
    starts = tuple(s for s in get_driver().config.command_start if s)

    async def _checker(event) -> bool:
        if getattr(event, "to_me", False):
            return True
        if not starts:
            return True
        text = event.get_plaintext().lstrip()
        return any(text.startswith(s) for s in starts)

    return Rule(_checker)

"""临时诊断插件：捕获 send_msg 调用栈，定位「参数...匹配失败」的发送者。

找到后删除本文件并恢复 bot.py。
"""

import traceback

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

_orig_call_api = Bot.call_api


async def _patched_call_api(self, api: str, **data):
    if api in ("send_msg", "send_group_msg") and "匹配失败" in str(data.get("message") or ""):
        logger.error(
            "[DIAG] 捕获可疑发送: api=%s user_id=%s group_id=%s text=%r\n%s"
            % (
                api,
                data.get("user_id"),
                data.get("group_id"),
                str(data.get("message"))[:120],
                "".join(traceback.format_stack()),
            )
        )
    return await _orig_call_api(self, api, **data)


Bot.call_api = _patched_call_api
logger.info("[DIAG] send_msg 调用栈捕获已启用")

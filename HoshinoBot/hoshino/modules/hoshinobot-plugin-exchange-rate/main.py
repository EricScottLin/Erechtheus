
from hoshino import Service

from .src.exchange_rate import help_message, handle_message

sv = Service(
    name="汇率",  # 功能名
    visible=True,  # 可见性
    enable_on_default=True,  # 默认启用
    bundle="工具",  # 分组归类
    help_=help_message,  # 帮助说明
)

@sv.on_prefix("汇率")
async def _(bot, event):
    reply = await handle_message(event.raw_message)
    if reply:
        await bot.send(event, reply)

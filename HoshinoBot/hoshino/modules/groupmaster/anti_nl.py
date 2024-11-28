from datetime import timedelta
from hoshino import Service, priv, util
from hoshino.typing import CQEvent, CQHttpError, MessageSegment as ms

sv = Service('anti-nl', enable_on_default=True)


@sv.on_keyword(['奶龙', '乃龙', '女乃龙', '奶农'])
@sv.on_rex(r'.*奶龙.*')
async def anti_holo(bot, ev: CQEvent):
    priv.set_block_user(ev.user_id, timedelta(minutes=1))
    await util.silence(ev, 120, skip_su=False)
    await bot.send(ev, '对')
    try:
        await bot.delete_msg(self_id=ev.self_id, message_id=ev.message_id)
    except CQHttpError:
        pass

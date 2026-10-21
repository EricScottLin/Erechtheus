"""吐槽插件（qqbot_roast）。

安全版「嘴臭」：不带脏字、不做人身攻击的整活吐槽。

- ``/骂 @某人`` / 回复消息发 ``/骂``：随机吐槽一句
- 不指定对象时吐槽发言者自己
- 默认关闭，复用 ``qqbot_group`` 的按群功能开关（``/本群功能``、``/开启 骂``）
- 自定义文案可放 ``data/qqbot_group/roasts.json``（JSON 字符串数组）
"""

from nonebot.plugin import PluginMetadata

from . import features as features  # noqa: F401  （注册按群开关）
from . import roast as roast  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="吐槽",
    description="安全版嘴臭：/骂 @某人 随机吐槽一句（默认关闭，按群开关）",
    usage="""\
/骂 @某人            随机吐槽一句（也可回复消息后发 /骂）
（不指定对象时吐槽发言者自己；默认关闭，管理员用 /开启 骂 打开）""",
    type="application",
    homepage="https://github.com/nonebot-qqbot",
    supported_adapters={"~onebot.v11"},
)

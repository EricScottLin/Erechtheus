"""表情包管理插件（qqbot_meme）。

统一的表情包库 + 金句库：

- **戳一戳 / 拍一拍**：被戳机器人头像时，从表情库随机抽一张回敬
- ``/表情``            随机来一张
- ``/表情列表``        查看表情库
- ``/表情添加``        回复图片发命令即可入库（群主/群管/超管）
- ``/表情删除 <序号>`` 删除（群主/群管/超管）
- ``/金句`` / ``/金句添加``  金句库（预留，数据文件 data/qqbot_meme/quotes.json）

表情包文件放在 localstore 数据目录的 ``stickers/`` 下，也可以直接手动丢图片进去，
插件每次使用时会重新扫描目录，无需重启。

遵循 NoneBot 官方插件编写规范：
https://nonebot.dev/docs/tutorial/create-plugin
"""

from nonebot import require

# 数据目录依赖 localstore；先确保它已加载
require("nonebot_plugin_localstore")

from nonebot.plugin import PluginMetadata  # noqa: E402

from .config import Config  # noqa: E402

# 导入即注册事件响应器
from . import essence as essence  # noqa: E402,F401
from . import library as library  # noqa: E402,F401
from . import poke as poke  # noqa: E402,F401
from . import providers as providers  # noqa: E402,F401
from . import quote as quote  # noqa: E402,F401
from . import sticker as sticker  # noqa: E402,F401

__plugin_meta__ = PluginMetadata(
    name="表情包管理",
    description="统一表情包库：戳一戳随机回图、/表情 管理；金句库（可自动收录群精华）",
    usage="""\
（戳一戳机器人头像会随机回一张表情包）
/表情                随机来一张
/表情列表            查看表情库
/表情添加            回复一张图后发送，收进表情库（管理员）
/表情删除 <序号>     删除表情（管理员）
/金句                随机一条金句
/金句添加 <内容>     收录金句（管理员）""",
    type="application",
    homepage="https://github.com/nonebot-qqbot",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

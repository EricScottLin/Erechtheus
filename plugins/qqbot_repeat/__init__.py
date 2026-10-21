"""复读插件（qqbot_repeat）。

从 ``qqbot_group`` 里拆出来的独立插件，包含两块能力：

- **连续复读**：连续多个不同用户发相同文本/图片时跟发一次
- **我说你说**：教机器人「听到 X 就回 Y」，Y 可以是文字，也可以是表情包图片

``repeat`` / ``teach`` 会注册进 ``qqbot_group`` 的按群功能开关（插件已加载时），
因此 ``/本群功能``、``/关闭 复读``、``/关闭 教学`` 仍然可用。
"""

from nonebot.plugin import PluginMetadata

from .config import Config

# 导入即注册各自的事件响应器（NoneBot 分包插件写法）
from . import features as features  # noqa: F401  （注册按群功能开关）
from . import repeat as repeat  # noqa: F401
from . import teach as teach  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="复读与我说你说",
    description="连续复读 + 我说X你说Y 教学（文字/表情包图片，按群隔离）",
    usage="""\
（复读无需命令：连续 3 个不同用户发相同文本/图片时自动跟发一次）
我说 <触发词> 你说           教机器人：听到触发词就回一张图（同时引用图片）
我说 <触发词> 你说 <文字>    教机器人：听到触发词就回这段文字
  例：我说 冰冰冰 你说（引用一张表情包）
  例：我说 你好 你说 你好呀
/我说列表                    查看本群所有教学
/我说删除 <触发词>           删除一条教学
/本群功能                    查看/开关复读、我说你说""",
    type="application",
    homepage="https://github.com/EricScottLin/Erechtheus",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

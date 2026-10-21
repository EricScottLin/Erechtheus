"""群聊管理插件（qqbot_group）。

一个插件收拢所有群聊管理能力：

- 禁言：``/烟``（8 小时内随机禁言）、``/禁言``（指定时长）、``/解禁``
- 踢人：``/踢``
- 全员禁言：``/全体禁言``、``/解除全体``
- 撤回：``/撤``（撤回机器人自己上一条消息）
- 按群功能开关：``/本群功能``、``/开启 <功能>``、``/关闭 <功能>``

复读与「我说X你说Y」教学已拆到独立插件 ``qqbot_repeat``。

权限模型：

    1. 仅群聊消息生效，私聊直接忽略
    2. 发送者必须是：群主 / 群管 / 机器人超级用户 / 配置的额外管理员
    3. 禁言、踢人、全员禁言还要求机器人自身是本群管理员；
       ``/撤`` 只撤回机器人自己的消息，因此只校验发送者

代码按功能拆分为子模块（mute / kick / whole / recall），
``__init__.py`` 负责插件元信息与导入注册。

遵循 NoneBot 官方插件编写规范：
https://nonebot.dev/docs/tutorial/create-plugin
"""

from nonebot.plugin import PluginMetadata

from .config import Config

# 导入即注册各自的事件响应器（NoneBot 分包插件写法）
from . import assets as assets  # noqa: F401
from . import box as box  # noqa: F401
from . import features as features  # noqa: F401
from . import greeting as greeting  # noqa: F401
from . import kick as kick  # noqa: F401
from . import mute as mute  # noqa: F401
from . import permissions as permissions  # noqa: F401  （注册发送记录钩子）
from . import recall as recall  # noqa: F401
from . import selfmute as selfmute  # noqa: F401
from . import whole as whole  # noqa: F401
from . import work as work  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="群聊管理",
    description="群聊管理：禁言/踢人/全体禁言、/撤 撤回、按群功能开关",
    usage="""\
/烟 @用户           随机禁言（默认 1 分钟 ~ 8 小时）
/禁言 @用户 [时长]   指定时长禁言，如 /禁言 @他 30m，默认 10 分钟
/解禁 @用户          解除禁言
/踢 @用户            移出群聊
/全体禁言            开启全员禁言
/解除全体            关闭全员禁言
/撤                  撤回我上一条消息（仅管理员；/撤 或 @机器人 撤）
/本群功能            查看本群功能开关
/开启 <功能>         开启本群某功能（管理员）
/关闭 <功能>         关闭本群某功能（管理员）
/盒 @用户            查这个人的 QQ 号与跨插件资产
说「精致睡眠」       随机 6~12 小时禁言自己（管理员可 @别人）
说「禁言套餐」       随机 1~60 分钟禁言自己（管理员可 @别人）
说「晚安」/「早安」  记录作息，按睡眠时长发月币（每天一次）
说「上班」/「下班」  打卡计算工时，按劳动法发工资/加班费""",
    type="application",
    homepage="https://github.com/EricScottLin/Erechtheus",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

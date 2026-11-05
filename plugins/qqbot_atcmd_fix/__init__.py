"""修复：群里「@机器人 + 命令」时命令解析失效。

## 问题

NoneBot 的 ``TrieRule.get_value``（``nonebot/rule.py``）只看 **message[0]**：

```python
message = event.get_message()
message_seg: MessageSegment = message[0]
if message_seg.is_text():          # ← @bot 时首段是 at 段，is_text() 为 False
    segment_text = str(message_seg).lstrip()
    if pf := cls.prefix.longest_prefix(segment_text):
        ...
```

群里 @ 机器人时，OneBot 把 at 段放在消息首位，于是整段解析被跳过，
返回空的 ``CMD_RESULT``（``command=None``），**所有 ``on_command`` 的
``CommandRule`` 必然为 False** —— 命令插件完全收不到。

注意这与 ``COMMAND_START`` 里是否含 ``""`` 无关：``@bot /status`` 这种
英文斜杠命令同样失效。

后果分两种：

- ``@bot /签到``：``qqbot_ai._is_command_message`` 能识别斜杠命令 → AI 闭嘴，
  而命令插件又收不到 → **完全静默**，用户以为机器人死了。
- ``@bot 签到``：``_is_command_message`` 识别不出裸词命令 → AI 抢答一句，
  **用户看到 AI 答非所问**。

## 修法

用 NoneBot 官方的事件预处理钩子 ``nonebot.message.event_preprocessor``。
它在 ``handle_event`` 内部、**紧挨着** ``TrieRule.get_value`` **之前**执行，
因此在这里把「指向自己的 at 段」从消息里摘掉，后续的命令解析就能正常工作。

选择全局钩子而不是逐个插件打补丁，原因：
  - ``nonebot_plugin_bilibili`` 早先就自己补了一层（``_at_cmd_rule``），
    但另外 11 个插件都没有；逐个补会漏、且新插件还要记得补。
  - 钩子一处生效，全部插件受益，新插件零成本。

## 安全边界（很重要）

只摘「at 机器人自己」的段，并且**只摘一次、只摘指向自己的**：

- at *别人*（``@张三 /签到``）**不动** —— 那是用户在称呼群友，命令本身仍应生效，
  而且不能把别人的 at 吃掉让插件失去引用信息。
- 同时 at 多人时，只移除指向自己的那些，其余顺序不变。
- 不限位置：不只 AT 在首位——「张三 @bot /签到」这种也应能识别。
- 非 OneBot 事件、非消息事件一律直接放行。
- ``event.to_me`` 保持原样（NoneBot 已算好，AI 插件依赖它）。

## 对 AI 插件的影响

``qqbot_ai._is_command_message`` 依赖 ``event.get_message().extract_plain_text()``。
去掉 at 段后，``@bot /签到`` 的纯文本从 ``"/签到"``（extract_plain_text 本来就会
跳过 at）**不变**；``@bot 签到`` 也仍是 ``"签到"``。所以本修复不会改变 AI 的判定，
只是让命令插件重新能命中 —— 命令先于 AI（priority 5 < 9）执行并 block，AI 自然不再抢答。
"""

from nonebot import get_plugin_config
from nonebot.adapters.onebot.v11 import Bot as OB11Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.message import event_preprocessor
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="@命令修复",
    description="修复「@机器人 + 命令」导致命令插件收不到的问题（全局事件预处理，无命令）",
    usage="（无命令，自动生效：修复 @机器人 后接命令时命令失效的问题）",
    type="library",
    homepage="https://github.com/EricScottLin/Erectheus",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

plugin_config: Config = get_plugin_config(Config)


def _strip_self_mentions(message: Message, self_id: str) -> tuple[Message, int]:
    """从消息中移除指向 ``self_id`` 的 at 段。

    返回 (新消息, 移除的段数)。没有可移除的段时原样返回。
    """
    if not message:
        return message, 0

    kept: list = []
    removed = 0
    for seg in message:
        if seg.type == "at" and str(seg.data.get("qq")) == str(self_id):
            removed += 1
            continue
        kept.append(seg)

    if removed == 0:
        return message, 0
    return Message(kept), removed


@event_preprocessor
async def _fix_at_prefixed_command(bot: OB11Bot, event: MessageEvent) -> None:
    """在任何 matcher 之前，去掉发给自己（机器人）的 @ 段。

    必须在这里做：``TrieRule.get_value`` 在 ``handle_event`` 里紧接着
    预处理之后调用，晚于本钩子就没有意义了。
    """
    if not plugin_config.qqbot_atcmd_fix_enabled:
        return
    if not isinstance(event, GroupMessageEvent):
        # 私聊没有 at 段问题；其他事件类型不需要处理
        return

    self_id = str(bot.self_id)
    message = event.get_message()
    stripped, removed = _strip_self_mentions(message, self_id)
    if not removed:
        return

    # 群聊里「只 @ 机器人、什么都没说」的情况：不动，交给各插件/AI 的原逻辑
    if not str(stripped).strip():
        return

    event.message = stripped
    # original_message 同步更新，避免插件读到带 at 的旧串而行为不一致
    event.original_message = stripped

# qqbot_atcmd_fix —— 「@机器人 + 命令」修复

无命令的基础设施插件。注册一个全局事件预处理钩子，让**群里 @ 机器人之后发命令**能正常工作。

## 修的是什么

NoneBot 的 `TrieRule.get_value`（`nonebot/rule.py`）只看消息的**第一段**：

```python
message = event.get_message()
message_seg: MessageSegment = message[0]
if message_seg.is_text():          # ← @bot 时首段是 at 段，is_text() 为 False
    segment_text = str(message_seg).lstrip()
    if pf := cls.prefix.longest_prefix(segment_text):
        ...
```

群里 @ 机器人时，OneBot 把 at 段放在消息首位，于是整段命令解析被跳过，
`command` 为 `None` → **所有 `on_command` 的 `CommandRule` 必然 False**。

注意这与 `COMMAND_START` 是否含 `""` **无关**：`@bot /status` 这种英文斜杠命令同样失效。

修复前的两种表现：

| 输入 | 结果 |
|---|---|
| `@bot /签到` `/status` `/ping` `/指令` | `qqbot_ai` 认出是斜杠命令 → AI 闭嘴；命令插件又收不到 → **完全静默** |
| `@bot 签到` `@bot 信息` | `qqbot_ai` 认不出裸词命令 → **AI 抢答**，答非所问 |
| `@bot /骂 @某人` | **完全静默** |

## 怎么修的

用 NoneBot 官方钩子 `nonebot.message.event_preprocessor`。它在 `handle_event` 内部、
**紧挨着** `TrieRule.get_value` 之前执行，因此在这里摘掉指向机器人的 at 段，后续命令解析即可正常工作。

选全局钩子而不是逐个插件打补丁的原因：

- `nonebot_plugin_bilibili` 早先就自己补了一层（`_at_cmd_rule`），但另外 11 个插件都没有；
  逐个补会漏，而且新插件还得记得补。
- 钩子一处生效，全部插件受益，新插件零成本。

## 安全边界

只摘「at 机器人自己」的段，并且只摘一次：

- at **别人**（`@张三 /签到`）**不动** —— 那是用户在称呼群友，不能把别人的 at 吃掉。
- 同时 at 多人时，只移除指向自己的，其余顺序不变。
- 不限位置：`张三 @bot /签到` 里的 at 也会被摘（但该场景 Messagesegment 首段是普通文本，
  命令本就无法解析，属修复范围之外的既有限制）。
- 「只 @ 机器人、什么都没说」不处理，交给各插件 / AI 的原逻辑。
- 非群聊消息、非 OneBot 事件一律直接放行。
- `event.to_me` 保持原样（AI 插件依赖它）。

对上层的可见影响：`extract_plain_text()` 本来就会跳过 at 段，所以 `@bot /签到` 的纯文本
在修复前后都是 `"/签到"`，AI 的判定不受影响；改变的是**命令插件重新能命中**，
命令 priority 5 先于 AI 的 9 执行并 block，AI 自然不再抢答。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `qqbot_atcmd_fix_enabled` | `true` | 关掉即恢复原行为（一般只用于排查） |

## 验证

```bash
docker exec -i -w /app qqbot-nonebot python - <<'EOF'
import sys; sys.path.insert(0, "/app")
# ... 构造 GroupMessageEvent(to_me=True)，message=[at(3194243533), "/签到"]
# 期望：由 qqbot_economy.handle_sign 响应，而不是 qqbot_ai
EOF
```

实测结果见 `docs/COMMAND_AUDIT_2026-11-05.md` §2 与本次修复的 commit。

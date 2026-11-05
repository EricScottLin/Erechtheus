# 命令关系与冲突审计报告（2026-11-05）

> 方法：三名审计员并行，A 静态穷举命令名/别名/优先级，B 语义与触发条件，
> C 在容器内用真实事件回放（124 条用例）验证。本文由 Lead 交叉复核后汇总，
> **已剔除 2 条被实测证伪的误报**（见 §6）。
>
> 全程只读审计，未修改任何插件代码。审计期间队员测试写入的脏数据已清理（见 §7）。

---

## 1. 结论摘要

| 维度 | 结论 |
|---|---|
| 跨插件命令名/别名重名 | **0 条**（220 个命令词全部不重名） |
| 多插件对同一条消息重复回复 | **0 条**（124 条用例中 >1 条消息的情况为 0） |
| 「@机器人 + 命令」 | **全部失效**（P0，见 §2） |
| 按群功能开关与实际行为不符 | **3 类**（见 §4） |
| 数据竞争（重复发币/重复喂养） | **3 处**（见 §5） |

最严重的不是"命令撞车"，而是 **@ 机器人时命令解析整体失效** —— 这类问题用户天天会碰到。

---

## 2. P0：「@机器人 + 命令」整体失效

### 现象

| 输入 | 实际结果 |
|---|---|
| `@bot /签到` `/status` `/ping` `/指令` `/信息` `/待办` | 命令插件**不响应**，AI 抢答一句 |
| `@bot 签到` `@bot 信息` `@bot 梗` `@bot 状态` | 命令插件**不响应**，AI 抢答一句 |
| `@bot /骂 @某人` | **完全静默**（连 AI 都不回） |
| `@bot 你好` | 正常（AI 回答） |
| `@bot /bilibili rank` | 正常（该插件自己写了补偿） |
| `@bot + B站链接` | 正常（同上） |

### 根因（两层叠加）

**第一层：`TrieRule` 只看 `message[0]`**

`nonebot/rule.py`：
```python
message = event.get_message()
message_seg: MessageSegment = message[0]
if message_seg.is_text():          # ← @bot 时首段是 at 段，is_text() 为 False
    segment_text = str(message_seg).lstrip()
    if pf := cls.prefix.longest_prefix(segment_text):
        ...
```

群里 @ 机器人时，OneBot 把 at 段放在 `message[0]`，于是整个解析被跳过，
返回空 `CMD_RESULT`（`command=None`）→ **所有 `on_command` 的 `CommandRule` 必然为 False**。

> ⚠️ 这与 `COMMAND_START` 是否含 `""` **无关**。带斜杠的 `/status` 同样失效。

**第二层：AI 抢答或闭嘴，救不回来**

`qqbot_ai/__init__.py:128` 的 `_is_command_message()` 设计意图是"命令交给插件，AI 不抢"，
但它的判定 `if not any(p and raw.startswith(p) for p in starts)` **主动跳过空前缀**：

| 消息 | `_is_command_message` | 最终结果 |
|---|---|---|
| `@bot /签到` | `True`（识别出斜杠命令） | AI **闭嘴** → 插件也不响应 → **静默** |
| `@bot 签到` | `False`（识别不出裸命令） | AI **抢答** → 用户看到 AI 答非所问 |

两个各自"正确"的判断，叠加出了错误结果。

### 为什么只有 bilibili 没事

`nonebot_plugin_bilibili/__init__.py:608` 有专门的补偿注释：

> ④ 补偿：群聊「@机器人 + /bilibili ...」—— on_command 要求消息首段是文本且以 / 开头；
> 群聊一旦带了 @，首段变成 at 段，命令规则就匹配不上了 —— 所以这里单独接住。

**这个问题早有人踩到，但只在 bilibili 一个插件里打了补丁**，其余 11 个插件都没有。

### 修复方向（按性价比）

1. **全局剥离 at 段**（推荐）：在 `handle_event` 之前或自定义 driver 钩子里，
   若 `message[0]` 是 at 段且 at 的是机器人自己，把该段移到消息末尾或删除。
   一处修复，全部插件受益。
2. **收窄 `COMMAND_START` 为 `["/"]`**：能减少裸词歧义，但**不能**修复 @ 失效（第一层是首段问题）。
3. 逐个插件加 `@` 补偿 matcher：工作量大、易漏，bilibili 就是这么干的，不建议推广。

---

## 3. 命令名层面的检查结果

### 3.1 重名冲突：0 条

22 个插件、164 个 matcher、220 个命令词，**跨插件重名 0 条**。
NoneBot 的 `TrieRule.add_prefix` 遇重复前缀只告警不覆盖（`nonebot/rule.py:77-88`），因此不存在互相覆盖。

### 3.2 唯一自重复（第三方，低）

`nonebot_plugin_nbnhhsh/handlers.py:61-62`：主命令与分组命令同词、同 priority=10、同 block=True。
属于第三方内部问题，不影响自有插件。

### 3.3 裸词误触风险（中，设计权衡）

`COMMAND_START=["/", ""]`（`.env.prod:12`）让所有中文命令可裸发。
实测已触发：`信息`→面板、`买`→购买、`b站`→B站帮助、`猜歌`→猜歌、`状态`→运行状态。

短裸词清单（≤2 字，日常聊天易撞）：`买 领 信息 状态 表情 梗 撤 烟 电 骂 盒 踢`。

**这是刻意设计**（让用户不用记斜杠），代价是聊天里说了这些词会被当命令。
若要收敛，可用 `qqbot_group/rules.py:6` 已有的 `explicit_prefix_or_at()` 逐条加约束。

---

## 4. 按群功能开关与实际行为不符

### 4.1 注册了开关但代码没检查（高）

| 功能 | 注册处 | 问题 |
|---|---|---|
| **`meme`（表情包）** | `qqbot_group/features.py:152` | `qqbot_meme/sticker.py:11` **只 import 了 `feature_gate` 却从未调用**。`/表情`、`/表情列表`、`/表情添加`、`/表情删除` 全部无视开关 —— 管理员关掉后仍然工作。 |

### 4.2 注册为 True 但实际不防（中）

| 功能 | 问题 |
|---|---|
| `bili_video` | `/关闭 视频解析` 写 `_GROUP_OFF`，但 `bili_at_cmd`（`bilibili/__init__.py:609`）**不读**该状态 → 用 `@bot bilibili <链接>` 能绕过关闭状态。 |

### 4.3 完全没有开关（中）

`qqbot_economy`、`qqbot_core`、`qqbot_pigverse`、`qqbot_toolbox`、`qqbot_maimai_dx`
**均未接入按群功能开关**，`/本群功能` 里看不到，也就无法按群关闭。

### 4.4 开关机制自身缺陷

- `features.py:175-177`：**未注册的 key 一律返回 `True`**，拼错的名字会静默"开"。
- `features.py:136-145`：`sync_external_features()` 有 `_external_checked` 一次性标志，
  首次调用后永久锁定，晚加载插件的功能可能永远注册不上。
- `features.py:188-200`：`set_enabled` 是"读全量→改一项→写全量"，**无锁**，
  两个管理员同时操作会互相覆盖（对比：`qqbot_repeat/store.py`、`qqbot_todo/store.py`、`qqbot_pigverse/store.py` 都有锁）。

---

## 5. 数据竞争（可能重复发钱）

| 位置 | 问题 | 后果 |
|---|---|---|
| `qqbot_pigverse/achievements.py:79-104` | "检查 claimed → 发 Kleos → 写 claimed" 三步不原子 | 并发时同一里程碑**发两次 Kleos** |
| `qqbot_pigverse/arena.py:61-69` | `get_arena → 判断 → update_arena`，判断在锁外 | 斗猪次数可超过每日 3 次 |
| `qqbot_pigverse/ranch.py:44-54` | 判断 `last_fed` 在锁外 | 同一天对同一只猪**喂两次、扣两次 Drachma** |

`qqbot_core` 的 `earn`/`spend`/`transfer` 单次调用是原子的（`storage.transact` 单锁串行），
**风险不在 core，在调用方的"检查-再执行"模式**。

另：`qqbot_core/storage.py` 的 `_cache` 是进程内单例，**必须单实例部署**，多实例会互相覆盖。
当前 compose 只起一个 nonebot 容器，符合要求。

---

## 6. 被实测证伪的误报（不要据此改代码）

| 曾报结论 | 实际 | 证伪方法 |
|---|---|---|
| `.env.prod` 的 `NBNHHSH_AUTO=false` 因大写不匹配前缀被忽略 → 自动划词实际是开 | **误报**。大写环境变量名能正常生效 | 反证实验：设 `NBNHHSH_AUTO=true`，运行时 `nbnhhsh_auto` 跟着变 `True`；`NBNHHSH_TEXT2PIC=false` 同样生效。机制是 `nonebot/config.py:86` 的 `case_sensitive=False` |
| `@bot /签到` 完全静默 | **只对一半**：斜杠命令是静默（AI 闭嘴 + 插件不响应），裸词命令是 AI 抢答 | 干净的 `to_me=True` 对照实验，无打桩干扰 |

> 教训：**不要因为"运行时值 == 默认值"就断定"配置被忽略"**。必须把值改成相反的再观察。
> 同理，构造测试事件时 **`to_me` 不会自动为 True**，必须显式传，否则整组用例都是假象。

### 看起来像冲突但实际不是（无需改）

- 「梗」vs「梗图」：同一 matcher 的主名 + alias
- 「踢」vs `kick` feature：后者是**功能开关别名**，从不注册成 matcher
- `qqbot_meme` 与 `qqbot_pigverse` 两个同名 `image_watcher`：均 priority=100、均 `block=False`，两个都跑、互不阻塞
- `qqbot_maimai_dx` 的 8 个 priority=8 matcher：用 `_plain(event) == name` **整句精确匹配**，是全项目最稳的写法，可作为整改模板

---

## 7. 审计期间的脏数据与清理

队员做真实事件回放时，部分用例写进了线上数据目录。已全部清理：

| 文件 | 污染 | 处理 |
|---|---|---|
| `data/qqbot_group/features.json` | 群 984616276 的 `repeat` 被测试改成 `false` | 从 12:03 的备份核对后回滚为 `true`；`todo` 是真实的功能启用，保留 |
| `data/qqbot_todo/tasks.json` | 9 条测试待办（`修bug` ×1、`x` ×8） | 备份中无该插件（说明原始为空），已清空并 `next_id` 重置为 1 |

已重启容器使清理生效，确认 `Bot 3194243533 connected`。

---

## 8. 建议排期

| 优先级 | 问题 | 修复成本 |
|---|---|---|
| **P0** | @机器人 + 命令整体失效（§2） | 中（建议做全局 at 剥离） |
| **P1** | `meme` 开关未接入（§4.1） | 小（补一处 `is_enabled`） |
| **P1** | pigverse 三处并发竞态（§5） | 中（改原子 claim 模式） |
| **P2** | 私聊任意图片被 maimai_dx 二维码 matcher 吞掉（`_private_image_rule` 只判断"有图"） | 中 |
| **P2** | 私聊 `小e 你好` 双回（`_wake_rule` 与 `_direct_rule` 都命中） | 小 |
| **P2** | economy/core/pigverse/toolbox 缺按群开关（§4.3） | 中 |
| **P3** | `features.set_enabled` 无锁；未注册 key 默认 True（§4.4） | 小 |
| **P3** | `/踢人` 被 `/踢` 前缀吞并，回复"功能被关闭"误导用户 | 小 |
| **P3** | 非超管发 `/关闭 待办` 被 todo 的 priority=3 matcher 抢走（§4） | 小 |
| **P3** | 裸词误触（§3.3） | 中（逐条加前缀约束） |

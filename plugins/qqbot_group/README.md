# qqbot_group — 群聊管理插件

NoneBot2 本地插件，把群聊管理相关能力收拢在一个模块里：

- **禁言**：`/烟` 随机禁言（8 小时内）、`/禁言` 指定时长、`/解禁`
- **踢人**：`/踢`
- **全员禁言**：`/全体禁言`、`/解除全体`
- **撤回**：`/撤` 撤回机器人自己上一条消息
- **复读**：连续相同文本达到阈值时跟着复读一次（默认 3 条、60 秒冷却）

## 命令

| 命令 | 别名 | 说明 |
|---|---|---|
| `/烟 @用户` | `/抽烟` `/吸烟` `/来一根` | **随机禁言**（默认 1 分钟 ~ 8 小时），也可回复消息后发送 |
| `/禁言 @用户 [时长]` | `/ban` | 指定时长禁言，如 `/禁言 @他 30m`；默认 10 分钟，最长 30 天 |
| `/解禁 @用户` | `/unban` `/解除禁言` | 解除禁言 |
| `/踢 @用户` | `/kick` `/移出群聊` | 移出群聊 |
| `/全体禁言` | `/全员禁言` `/muteall` | 开启全员禁言 |
| `/解除全体` | `/取消全体禁言` `/unmuteall` 等 | 关闭全员禁言 |
| `/撤` | `/撤回` | **撤回机器人自己上一条消息**（仅管理员；支持 `/撤` 或 `@机器人 撤`，裸「撤」不触发） |

目标用户三种指定方式（优先级从高到低）：**回复某条消息** > **@ 用户** > **直接写 QQ 号**。

## 按群功能开关

每个群可以单独关闭/开启某些功能（群主 / 群管 / 超管可用）：

```
@机器人 关闭本群视频解析      # 自然语言，@ 或不 @ 都行
/关闭 复读
/开启 表情包
/本群功能                    # 查看本群全部开关
```

当前支持的功能：

| key | 名称 | 说明 |
|---|---|---|
| `repeat` | 复读 | 连续相同文本/图片达到阈值时复读 |
| `poke` | 戳一戳表情 | 被戳时随机回表情/金句 |
| `quotes` | 金句 | `/金句` 与戳一戳抽金句 |
| `meme` | 表情包 | `/表情` 随机表情 |
| `box` | 盒 | `/盒 @用户` 查 QQ 号 |
| `bili_video` | 视频解析 | 桥接 B 站插件的按群开关（同一份状态） |

- 状态存在 `data/qqbot_group/features.json`（仅记录被改过的群），重启不丢
- 其他插件可注册自己的功能：

```python
from nonebot import get_plugin

features = get_plugin("qqbot_group").module.features
features.register_feature("my_feature", "我的功能", default=True)

# 处理消息前判断
if not features.is_enabled(group_id, "my_feature"):
    return
```

## 资产聚合（/盒）

`/盒 @用户` 会遍历所有已注册的资产来源，把该用户在各插件里的家底拼到一起：

```
📦 开盒成功：@某人 的 QQ 是 123456
──── 资产 ────
【qqbot_core】
🏛 荣耀：102
🌙 月币：90,188
🐟 鱼币：80
【qqbot_economy】
📅 签到：连续 1 天 · 累计 1 天
🎣 鱼竿 Lv1 · 鱼饵 0
🐟 累计钓鱼 1 条 · 最佳 月光比目
【qqbot_pigverse】
🐷 猪猪图鉴：2 只
```

内置来源：`qqbot_core` 钱包、`qqbot_economy` 玩法数据、`qqbot_pigverse` 猪猪宇宙。
其他插件可以注册自己的资产来源：

```python
from nonebot import get_plugin

assets = get_plugin("qqbot_group").module.assets

async def my_assets(uid: str) -> list[str]:
    return [f"我的代币：{...}"]

assets.register_asset_provider("my_plugin", my_assets)
```

单个来源异常会被跳过，不影响其他来源。

## 复读

无需命令：同一群内连续 **`qqbot_group_repeat_times` 个不同用户**（默认 3 人）
发来相同的文本或**同一张表情包/图片**时，机器人会跟着复读一次，
随后进入冷却（默认 60 秒）。

- 文本与图片/表情都支持：图片按 QQ 的内容哈希文件名识别，表情按 id 识别
- 按**不同用户数**计数：同一个人连发同样的内容不会触发
- 以 `/` 开头的命令、空消息、超长文本、图文混合消息不参与（会重置计数）
- 可用 `qqbot_group_repeat_enabled=false` 关闭，或用
  `qqbot_group_repeat_include_images=false` 只复读文本

## 权限模型

1. **仅群聊生效**，私聊消息直接忽略
2. **发送者**必须是：群主 / 群管 / 机器人超级用户（`SUPERUSERS`）/ 配置的额外管理员
3. **禁言、踢人、全员禁言**还要求机器人自身是本群管理员
   （每次执行前实时调用 `get_group_member_info` 检查）
4. **`/撤`** 只撤回机器人自己的消息，不需要机器人是管理员，仅校验发送者

## 配置

配置项经环境变量 / `.env` / `.env.prod` 注入，前缀 `qqbot_group_`：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `qqbot_group_smoke_min_seconds` | int | `60` | `/烟` 随机禁言时长下限（秒） |
| `qqbot_group_smoke_max_seconds` | int | `28800` | `/烟` 随机禁言时长上限（秒，8 小时） |
| `qqbot_group_ban_default_seconds` | int | `600` | `/禁言` 未指定时长时的默认禁言（秒） |
| `qqbot_group_extra_admins` | set[int] | 空 | 额外允许使用群管命令的 QQ 号，支持 `123,456` 或 `[123,456]` |
| `qqbot_group_repeat_enabled` | bool | `true` | 是否启用复读 |
| `qqbot_group_repeat_include_images` | bool | `true` | 复读是否包含图片/表情 |
| `qqbot_group_repeat_times` | int | `3` | 连续多少条相同文本触发复读（2–10） |
| `qqbot_group_repeat_cooldown` | int | `60` | 同一群两次复读的最小间隔（秒） |
| `qqbot_group_repeat_max_len` | int | `200` | 超过该长度的消息不参与复读 |

## 实现要点

- 禁言走 OneBot `set_group_ban`（`duration=0` 即解禁），踢人走 `set_group_kick`，
  全员禁言走 `set_group_whole_ban`
- `/撤` 通过 NoneBot 的 `Bot._called_api_hook` 记录每次 `send_msg`/`send_group_msg`
  返回的 `message_id`（按群），再调用 `delete_msg` 撤回；只记录本次进程启动后的消息，
  受 QQ「2 分钟内可撤回」限制
- 对群主或其他管理员执行禁言/踢人时，OneBot 会返回 `ActionFailed`，插件捕获后友好提示
- 时长解析支持 `45`、`30m`、`2小时`、`1d` 等中英文写法

## 结构

```
plugins/qqbot_group/
├── __init__.py      # 插件元信息 + 导入注册
├── config.py        # pydantic 配置模型（qqbot_group_ 前缀）
├── helpers.py       # 纯逻辑：目标解析 / 时长解析与格式化 / 权限判定
├── permissions.py   # 权限闸门 + 机器人发送记录钩子
├── mute.py          # /烟 /禁言 /解禁
├── kick.py          # /踢
├── whole.py         # /全体禁言 /解除全体
├── recall.py        # /撤
├── repeat.py        # 复读
└── README.md
```

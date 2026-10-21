# qqbot_repeat — 复读 + 我说你说

从 `qqbot_group` 里拆出来的独立插件，包含两块能力：

- **连续复读**：连续多个不同用户发相同文本/图片时跟发一次
- **我说你说**：教机器人「听到 X 就回 Y」，Y 可以是文字，也可以是**表情包图片**

`repeat` / `teach` 会注册进 `qqbot_group` 的按群功能开关，所以
`/本群功能`、`/关闭 复读`、`/关闭 教学` 仍然可用。

## 命令

| 命令/触发 | 权限 | 说明 |
|---|---|---|
| （自动复读） | — | 连续 3 个不同用户发相同文本/图片 → 跟发一次，60 秒冷却 |
| `我说 <触发词> 你说` | 群主/群管/超管 | 教一句：**引用一张表情包/图片**作为回复 |
| `我说 <触发词> 你说 <文字>` | 群主/群管/超管 | 教一句：回复指定文字 |
| `/我说列表` | 所有人 | 查看本群所有教学 |
| `/我说删除 <触发词>` | 群主/群管/超管 | 删除一条教学 |
| `/本群功能` | 群管 | 查看/开关复读与教学 |

## 我说你说

### 教表情包

```
我：我说 冰冰冰 你说          （同时引用一张表情包）
机器人：✅ 记住了：有人说「冰冰冰」就发这张图（本群第 1 条）
群友：冰冰冰
机器人：[那张表情包]
```

三种给图方式都行：

1. **引用一张图**：`我说 冰冰冰 你说` + 引用表情包（最常用）
2. **图一起发**：`我说 冰冰冰 你说` + 同一条消息里带图
3. **引用文字**：`我说 你好 你说` + 引用一条文字消息，回复那段文字

### 教文字

```
我说 你好 你说 你好呀
```

之后有人发「你好」，机器人回「你好呀」。

### 触发规则

- **按群隔离**：在 A 群教的内容不会在 B 群触发
- 默认 **整句完全匹配**（忽略首尾空白）；`qqbot_repeat_teach_match=contains`
  可以改成「消息包含触发词就触发」，多个命中时取最长的触发词
- 触发词不能以 `/` 开头，不能包含「我说」「你说」，最长 32 字
- 同一触发词默认 3 秒冷却，避免刷屏
- 教学命令本身不参与连续复读

### 权限

默认只有 **群主 / 群管 / 机器人超级用户 / 额外管理员** 能教和删：

```conf
qqbot_repeat_teach_admin_only=true
qqbot_repeat_extra_admins=123456,789012
```

额外管理员会与 `qqbot_group_extra_admins` 合并，不用配两份。
想让所有人都能教，设为 `false`。

## 连续复读

无需命令：同一群内连续 **`qqbot_repeat_times` 个不同用户**（默认 3 人）
发来相同的文本或**同一张表情包/图片**时，机器人跟发一次，随后进入冷却（默认 60 秒）。

- 文本与图片/表情都支持：图片按 QQ 的内容哈希文件名识别，表情按 id 识别
- 按**不同用户数**计数：同一个人连发同样的内容不会触发
- 以 `/` 开头的命令、空消息、超长文本、图文混合消息不参与（会重置计数）
- 可用 `qqbot_repeat_enabled=false` 关闭，或用
  `qqbot_repeat_include_images=false` 只复读文本

## 按群功能开关

复读插件加载后会把两个功能注册进 `qqbot_group`：

```
@机器人 关闭本群复读
/关闭 教学
/本群功能
```

| key | 名称 | 说明 |
|---|---|---|
| `repeat` | 复读 | 连续相同文本/图片达到阈值时复读 |
| `teach` | 我说你说 | 教学与触发 |

没加载 `qqbot_group` 时插件仍可独立工作，此时功能视为默认开启。

## 数据目录

由 `nonebot_plugin_localstore` 提供，容器内为 `/app/data/qqbot_repeat`，
对应宿主机 **`/srv/qqbot/data/qqbot_repeat`**：

```
data/qqbot_repeat/
├── teach.json     # 按群保存的教学规则
└── images/        # 教学用的表情包图片，文件名 = 内容 sha1 前 16 位
```

`teach.json` 结构：

```json
{
  "984616276": {
    "冰冰冰": {"kind": "image", "value": "ab12cd34ef56.png", "by": "10001", "at": 1700000000},
    "你好":   {"kind": "text",  "value": "你好呀",             "by": "10001", "at": 1700000001}
  }
}
```

- 图片在教学时就下载落盘，不依赖 QQ 原链接（原链接会过期）
- 文件名是内容哈希，同一张图重复教不会重复占空间
- 规则按群隔离；`by` / `at` 记录教学人和时间，方便以后做审计

## 配置

前缀 `qqbot_repeat_`，经环境变量 / `.env` / `.env.prod` 注入：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `qqbot_repeat_enabled` | bool | `true` | 是否启用连续复读 |
| `qqbot_repeat_include_images` | bool | `true` | 连续复读是否包含图片/表情 |
| `qqbot_repeat_times` | int | `3` | 连续多少个不同用户触发（2–10） |
| `qqbot_repeat_cooldown` | int | `60` | 同一群两次复读的最小间隔（秒） |
| `qqbot_repeat_max_len` | int | `200` | 超过该长度的文本不参与复读 |
| `qqbot_repeat_teach_enabled` | bool | `true` | 是否启用我说你说 |
| `qqbot_repeat_teach_admin_only` | bool | `true` | 是否只有管理员能教/删 |
| `qqbot_repeat_teach_match` | str | `exact` | `exact` 整句匹配 / `contains` 包含匹配 |
| `qqbot_repeat_teach_cooldown` | int | `3` | 同一触发词两次回复的最小间隔（秒） |
| `qqbot_repeat_teach_max_rules` | int | `100` | 每个群最多保存多少条教学 |
| `qqbot_repeat_teach_max_trigger_len` | int | `32` | 触发词最大长度 |
| `qqbot_repeat_teach_max_reply_len` | int | `500` | 文字回复最大长度 |
| `qqbot_repeat_teach_max_mb` | int | `5` | 教学图片单文件大小上限（MB） |
| `qqbot_repeat_extra_admins` | set[int] | 空 | 额外允许教/删的 QQ 号 |

## 实现要点

- 教学响应器 `priority=10`、触发响应器 `priority=11`，都 `block=True`：
  命中后不会再走到 `priority=90` 的连续复读，避免「教学 + 复读」双重回复
- 命令响应器 `priority=5` 先于教学/触发执行，所以教学不会抢 `/本群功能` 等命令
- 被引用消息优先用适配器填好的 `event.reply`，没有则从 `reply` 段拿
  `message_id` 调 `get_msg` 兜底
- 发送本地图片用 **base64**（`MessageSegment.image(bytes)`）：
  LLBot 与 NoneBot 是两个容器、文件系统不互通，`file://` 不可用
- 文件格式按文件头嗅探（PNG/JPG/GIF/WEBP），不信任后缀
- `teach.json` 用内存缓存 + mtime 失效，普通消息触发时不用每条都读盘
- 写入用临时文件 + `replace` 原子替换，并用 `asyncio.Lock` 串行化

## 与市面复读/问答插件的取舍

拆插件前看过几个同类实现，主要借鉴点：

| 插件 | 做法 | 本插件的取舍 |
|---|---|---|
| [nonebot-plugin-repeater](https://github.com/Utmost-Happiness-Planet/nonebot-plugin-repeater) | 群白名单、最小长度、次数；文本/QQ表情/图片 +1 | 保留「不同用户计数 + 图片复读」，改成按群开关，不再维护群白名单 |
| [nonebot-plugin-plus-one](https://github.com/yejue/nonebot-plugin-plus-one) | 40 行极简复读，支持转发/分享 | 保持实现轻量；不处理转发卡片，避免协议端差异 |
| [nonebot-plugin-word-bank2](https://github.com/MWTJC/nonebot-plugin-word-bank2) | `问…答…` 词库：全匹配/模糊/正则/全局/@，支持图片回复 | 只取「教学 + 图片回复 + 按群隔离 + 管理员权限」；不做正则/全局词库，保持「我说…你说…」这一种自然句式 |
| [HoshinoBot](https://github.com/SilenceChen/HoshinoBot) 的复读模块 | 经典 +1 复读 | 借鉴「不同用户才计数」的防刷思路 |

本插件刻意没有做的：正则触发、跨群全局规则、转发消息复读、按用户私聊词库。
需要更复杂的问答时，word-bank2 那类词库插件更合适；这里专注群聊里
「一句话教表情包」的轻量玩法。

## 结构

```
plugins/qqbot_repeat/
├── __init__.py      # 插件元信息 + 导入注册
├── config.py        # pydantic 配置（qqbot_repeat_ 前缀）
├── store.py         # teach.json + images/ 的存取
├── features.py      # 接入 qqbot_group 的按群功能开关
├── permissions.py   # 教学/删除权限
├── repeat.py        # 连续复读
├── teach.py         # 我说你说：教学、触发、列表、删除
└── README.md
```

## 测试

```bash
.venv/bin/python test_repeat.py   # 53 项
.venv/bin/python test_group.py    # 114 项（复读用例已迁出）
.venv/bin/python test_meme.py     # 45 项
```

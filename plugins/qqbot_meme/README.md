# qqbot_meme — 表情包管理插件

统一的表情包库，附带预留的金句库：

- **戳一戳 / 拍一拍**机器人头像 → 从表情库随机抽一张回敬（带冷却）
- `/表情` 随机来一张；`/表情列表` 查看；管理员可 `/表情添加`、`/表情删除`
- `/金句`、`/金句添加` 金句库（预留，数据文件已就位）

## 命令

| 命令 | 别名 | 权限 | 说明 |
|---|---|---|---|
| （戳一戳） | — | 所有人 | 戳机器人时随机回一张表情包（默认 3 秒冷却） |
| `/表情` | `/发表情` | 所有人 | 从**所有图片来源**随机来一张（本地 + 其他插件表情库） |
| `/表情列表` | — | 所有人 | 查看表情库总览（各来源数量、权重）与本地文件列表 |
| `/表情添加` | `/添加表情` | 群主/群管/超管 | 三种方式：回复一张图 / 先发图 10 分钟内发命令 / 图片和命令一起发 |
| `/表情删除 <序号>` | `/删除表情` | 群主/群管/超管 | 按 `/表情列表` 里的序号删除 |
| `/金句` | — | 所有人 | 随机一条**本群**金句，带发送者昵称（空库时提示） |
| `@某人 说过什么` | — | 所有人 | 发出该用户在本群说过的一句金句（也可回复消息查询） |
| `/金句添加 <内容>` | `/添加金句` | 群主/群管/超管 | 收录一条金句（归入当前群） |
| `/金句同步` | `/同步金句` | 群主/群管/超管 | 立即把群精华收进金句库（不等定时任务） |

## 数据目录

由 `nonebot_plugin_localstore` 提供，容器内为 `/app/data/qqbot_meme`，
对应宿主机 **`/srv/qqbot/data/qqbot_meme`**：

```
data/qqbot_meme/
├── stickers/            # 表情包文件（png/jpg/gif/webp）
├── stickers.json        # 预留：表情包元数据索引（当前扫描目录即可，未使用）
├── quotes.json          # 金句库：JSON 数组（字符串或 {text, author}）
└── essence_seen.json    # 已收录过的群精华 message_id（去重用）
```

- **手动添加**：把图片直接丢进 `stickers/` 即可，插件每次使用都会重新扫描目录，无需重启
- **命令上传**：`/表情添加` 会自动下载图片、按内容哈希命名（天然去重）；
  支持「回复图片」「先发图再发命令（10 分钟内）」「图文一起发」三种方式，成功后会把图片回显一遍
- **金句库格式**（`quotes.json`）：

```json
[
  {"text": "今天的风儿甚是喧嚣", "author": "群友A", "group_id": 984616276, "group_name": "Erectheion神庙"},
  {"text": "人类的本性就是复读机", "author": "群友B", "group_id": 995775691}
]
```

**金句按群隔离**：`/金句` 只抽当前群的金句，戳一戳抽到金句时也只从当前群取；
`/金句添加` 默认归入当前群（私聊添加则不带群号，任何群都能抽到）。

`/金句` 会显示 `—— 发送者昵称`；字符串与 `{"text": ...}` 两种写法都支持，以后可在此基础上扩展「谁说的/什么时候」等字段。

## 联动其他表情库 / 金句库

戳一戳不是只读本地 `stickers/`，而是从「来源池」按权重抽取：

| 来源 | 类型 | 说明 |
|---|---|---|
| `local` | 图片 | `data/qqbot_meme/stickers/` 本地表情包 |
| `quotes` | 文本 | 金句库（含群精华自动收录的内容） |
| `pigverse` | 图片 | 自动探测 `plugins/qqbot_pigverse` 的猪猪表情库（已加载才生效） |

默认图片:金句 = 3:1（可配置）。`/表情` 只从图片来源里抽，`/金句` 只从金句库抽（按群隔离）。

图片来源默认**按库大小加权**（`qqbot_meme_source_weighting=count`）：
本地 1 张 + pigverse 41 张时，抽到本地的概率约 1/42，等价于「从整个表情库随机抽一张」。
想让每个来源等概率就设为 `uniform`。

### 给未来的表情库/金句库留的接口

任何插件加载后都可以注册自己的来源：

```python
from nonebot import get_plugin

meme = get_plugin("qqbot_meme")
providers = meme.module.providers

async def my_stickers(ctx):
    path = ...  # 从你的库里取一张图；ctx.group_id 可用于按群隔离
    if path is None:
        return None
    return providers.ReplyItem("image", path.read_bytes(), "my_lib")

async def my_quotes(ctx):
    text = ...  # 从你的金句库取一条（可用 ctx.group_id 过滤）
    return providers.ReplyItem("text", text, "my_quotes") if text else None

providers.register_image_provider("my_lib", my_stickers)
providers.register_text_provider("my_quotes", my_quotes)
```

## 群精华自动收录

OneBot v11 没有「设为精华」的通知事件，插件用定时轮询实现：

1. `get_group_list` 拿机器人所在的群
2. `get_essence_msg_list` 拿每群精华（只有 message_id / 发送者等元数据）
3. `get_msg(message_id)` 取正文
4. 纯文本自动写进金句库（带作者），图片类精华跳过

已处理的 message_id 记在 `essence_seen.json`，不会重复收录；首次启动会把**现有精华全部导入**。
条目会存 `author_id`（发送者 QQ），`@某人 说过什么` 优先按 QQ 精确匹配；早期没有 `author_id` 的旧条目会按群名片/昵称回退，并在下次同步时自动回填。
轮询默认每 60 秒一次，也可以用 `/金句同步` 手动立即触发。

## 配置

配置项经环境变量 / `.env` / `.env.prod` 注入，前缀 `qqbot_meme_`：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `qqbot_meme_poke_enabled` | bool | `true` | 戳一戳是否随机回图 |
| `qqbot_meme_poke_cooldown` | int | `3` | 同一会话两次回图的最小间隔（秒，0–60） |
| `qqbot_meme_max_mb` | int | `5` | 表情包上传/随机发送的单文件大小上限（MB，1–20） |
| `qqbot_meme_quote_enabled` | bool | `true` | 是否启用 `/金句` |
| `qqbot_meme_essence_sync` | bool | `true` | 是否定时把群精华收进金句库 |
| `qqbot_meme_essence_interval` | int | `60` | 群精华轮询间隔（秒，30–3600） |
| `qqbot_meme_poke_image_weight` | int | `3` | 戳一戳抽到图片（含外部表情库）的权重 |
| `qqbot_meme_poke_quote_weight` | int | `1` | 戳一戳抽到金句的权重 |
| `qqbot_meme_source_weighting` | str | `count` | 来源加权方式：`count` 按库大小 / `uniform` 等概率 |

## 实现要点

- 戳一戳走 OneBot `notice_type=notify`、`sub_type=poke` 通知，判断
  `target_id == bot.self_id` 才响应；按群号（私聊按 QQ 号）做冷却
- 发送本地表情时用 **base64**（`MessageSegment.image(bytes)`），
  因为 LLBot 与 NoneBot 是两个容器、文件系统不互通，`file://` 路径不可用
- 文件格式按文件头嗅探（PNG/JPG/GIF/WEBP），不信任后缀
- 文件名 = 内容 sha1 前 16 位，上传同一张图不会重复入库
- 群精华同步只取纯文本；精华列表接口不返回正文，需逐条 `get_msg`

## 结构

```
plugins/qqbot_meme/
├── __init__.py      # 插件元信息 + 导入注册
├── config.py        # pydantic 配置模型（qqbot_meme_ 前缀）
├── library.py       # 表情包目录扫描 / 存取 + 金句库读写
├── providers.py     # 来源注册表：本地/金句/外部表情库（pigverse 等）
├── permissions.py   # 管理员判定
├── poke.py          # 戳一戳随机回图
├── sticker.py       # /表情 /表情列表 /表情添加 /表情删除
├── quote.py         # /金句 /金句添加
└── README.md
```

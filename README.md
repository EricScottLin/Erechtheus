# Erechtheus

QQ 机器人部署项目 —— **LLBot**（接入层，无头/direct 模式）+ **NoneBot2**（业务层）。

灵感来自希腊神话中的雅典国王 **Erechtheus（厄瑞克透斯）**。

## 架构

```
QQ 服务器
   │  NTQQ 协议（无头）
   ▼
LLBot  (linyuchen/llbot)          ← 接入层，跑 QQ 协议
   │  OneBot v11 正向 WebSocket
   │  ws://127.0.0.1:3001（仅回环，带 access token）
   ▼
NoneBot2  (python:3.10-slim)      ← 业务层，插件与命令
   │  内置 HTTP 服务 127.0.0.1:8080（bilibili 插件依赖 fastapi）
   ▼
plugins/
```

两个容器都用 `network_mode: host`，**不发布任何端口**；
LLBot 的 WebUI(3081) 与 OneBot WS(3001) 由配置文件强制绑定 `127.0.0.1`，公网不可探测。

## 插件

| 插件 | 来源 | 功能 |
|---|---|---|
| `qqbot_toolbox` | 自研 | `/指令`（**按指令列出全部用法**，`/指令 <指令名>` 看详情）、`/ping`（探活 + 延迟 + 拥堵评价）、`/status`（收发消息数、已运行时长）、`/echo` |
| `qqbot_core` | 自研 | **基础服务**：月币/荣耀账户、`/余额`、`/名片`、`/称号`、`/升阶`、`/发红包`、`/抢红包`、`/签名`、`/改昵称`、`/配置`（其他插件通过 `require` 调用其货币接口） |
| `qqbot_economy` | 自研 | 每日签到领荣耀、捕鱼赚月币、鱼竿/鱼饵/抽奖 |
| `qqbot_group` | 自研 | **群聊管理**：`/烟` 随机禁言（8 小时内）、`/禁言`、`/解禁`、`/踢`、`/全体禁言`、`/解除全体`、`/撤`（撤回机器人上一条消息）、**按群功能开关**（`/本群功能`、`/关闭 视频解析`）、`/盒`（查 QQ 号 + 跨插件资产） |
| `qqbot_repeat` | 自研 | **复读与我说你说**：连续 3 个不同用户发相同内容时跟发一次；`我说 X 你说 Y` 教学（Y 支持文字或表情包），`/我说列表`、`/我说删除` |
| `qqbot_meme` | 自研 | **表情包管理**：戳一戳随机回表情包、`/表情` 管理、金句库（群精华自动收录） |
| `qqbot_roast` | 自研 | **吐槽/电猪/梗图**：`/骂`、`/电`、`/梗`（私聊上传触发词→表情包）（默认关闭，按群开关） |
| `qqbot_pigverse` | 自研 | **小猪宇宙**：图鉴成就 → 荣耀、吃什么/喝什么推荐、表情库总览拼图 |
| `nonebot_plugin_bilibili` | vendored + 自研扩展（[GreaterLG70/nonebot-plugin-bilibili](https://github.com/GreaterLG70/nonebot-plugin-bilibili) 之上加了「发视频」） | 群里发B站链接 → 回 `y` 发压缩版视频；`/bilibili rank` 榜单；`/bilibili up` 查 UP 主 |
| `nonebot_plugin_picmenu_next` | [lgc-NB2Dev/nonebot-plugin-picmenu-next](https://github.com/lgc-NB2Dev/nonebot-plugin-picmenu-next) | `/help`、`/帮助`、`/菜单` 图片帮助菜单（插件列表/详情/模糊搜索） |
| `nonebot_plugin_rollpig_plus` | [Felis2026/nonebot-plugin-rollpig-plus](https://github.com/Felis2026/nonebot-plugin-rollpig-plus) | 今日小猪：每日抽猪、猪圈收藏、烤群友 |
| `nonebot_plugin_nbnhhsh` | [ByteColtX/nonebot-plugin-nbnhhsh](https://github.com/ByteColtX/nonebot-plugin-nbnhhsh) | 「能不能好好说话」拼音缩写翻译：`/nbnhhsh yyds`、`/好好说话 yyds`、「yyds是啥」；默认出图 |
| `nonebot_plugin_maimaidx` | [Yuri-YuzuChaN/nonebot-plugin-maimaidx](https://github.com/Yuri-YuzuChaN/nonebot-plugin-maimaidx) | 舞萌 DX 查分器 |
| `qqbot_maimai_dx` | 自研 + [MaimaiDXCHN](https://github.com/EricScottLin/MaimaiDXCHN)（git submodule） | **舞萌官方服直连**：`绑定`（私聊发二维码图片）/`大象信息`（资料+状态）/`更新`/`导出`/`登出`/`识图`/`解绑`。只在指定群可见（其他群完全静默、不进帮助菜单），未绑定先提示绑定，按月币收费（说明/大象信息免费），绑定需荣耀 Lv.10 |

> **为什么舞萌直连另起一条线**：`nonebot_plugin_maimaidx` 走水鱼/落雪查分器（第三方归档），
> 只能看到别人上传过的成绩；`qqbot_maimai_dx` 直连官方服机台协议，能读官方资料/成绩、
> 生成待确认的查分器同步预览、导出 JSON/CSV 并做登出核验。代价是要用微信「舞萌DX」
> 小程序的二维码绑定，真实读取一次约 1 分钟。两条线互不影响，可同时开。

> **为什么 bilibili 插件是 vendored 的**：PyPI 上的 `nonebot-plugin-bilibili`
> 是 `mengbingnaixi` 的「动态订阅」插件，与本文需要的「视频解析」插件**同名不同物**，
> 直接 `pip install` 会装错。故将源码放入 `plugins/`，由 `bot.py` 用 `Path` 加载，
> 同时避开包名冲突。

## B站视频发送（2026-10 新增）

有人在群里发 B站链接（`b23.tv` 短链 / `bilibili.com/video/BV...` / `av` 号都行），机器人会：

1. 回复一条**信息卡**（标题 / UP主 / 播放 / 点赞 / 弹幕 / 时长）
2. 提示「回 y 发 480P 压缩版（约 N MB）；要高画质回 y 720p / y 1080p」
3. 用户回 `y` / `y 720p` / `y 1080p` 后，ffmpeg 直连 CDN 边下边转成 mp4 → 发到群里

画质档位（`y` 后可带）：

| 回复 | 画质 | 体积上限 | 是否需要账号 |
|---|---|---|---|
| `y` | 480P | 20MB | 不需要（匿名可用） |
| `y 720p` | 720P | 40MB | **需要** `BILI_SESSDATA`（普通账号） |
| `y 1080p` | 1080P | 80MB | **需要** `BILI_SESSDATA` + 通常是大会员 |
| `y 360p` | 360P | 15MB | 不需要 |

拿不到请求的画质时会自动降级，并回一条「⚠️ 这个视频最高只有 XP」的说明。

获取 `BILI_SESSDATA`：浏览器登录 bilibili.com → F12 → Application → Cookies →
复制 `SESSDATA` 的值填进 `.env`，然后 `docker compose up -d nonebot`。

三种写法都认，粘贴时不用自己裁：

```bash
BILI_SESSDATA=a1b2c3%2C1792...%2Cxy12%2A31                        # 纯值
BILI_SESSDATA=SESSDATA=a1b2c3%2C1792...%2Cxy12%2A31               # 键值对
BILI_SESSDATA="SESSDATA=..; bili_jct=..; DedeUserID=.."          # 整段 Cookie
```

填完重启时日志会自检：

```
[bilibili] B站账号已登录：牧歌（大会员）。720P 可用，1080P 需大会员
[bilibili] BILI_SESSDATA 无效或已过期（账号未登录），画质回落到 480P
```

`buvid3`/`buvid4` 不用管，插件会自动取。

默认**不 @ 机器人也会响应**（群聊），但要求发链接者回 `y` 确认，避免刷屏和大流量下载。

实现要点（踩坑记录见 `plugins/nonebot_plugin_bilibili/README.md`）：

- 视频详情走 **wbi 签名的 `x/web-interface/wbi/view`** —— 裸 `view` 接口从机房 IP 请求会 412
- 还要带 **buvid3/buvid4 设备指纹**（自动从 `x/frontend/finger/spi` 取，缓存到
  `data/bili_buvid.json`）—— 不带的话机房 IP 连发必 412，现象就是「解析失败：B站临时风控」
- 播放地址走 `x/player/playurl`：**匿名最高只有 480P**（实测请求 qn=64/80 也只回
  360P/480P），配了 `BILI_SESSDATA` 才能拿 720P，1080P 还要大会员
- **ffmpeg 直连 CDN 边下边转**（带 Referer/UA），不再「先下载完再转码」——
  2026-10 实测同一视频从 23s 降到 14.7s / 43s 降到 17.5s
- 发送用 `base64://` 交给 LLBot（LLOneBot 的 `uri2local` 支持 base64/http/file 三种）
- 全程单并发（`asyncio.Lock`），避免多人同时发链接打满 CPU

> 速度上限在两条网线上：下载 B站 CDN 约 1MB/s（按 IP 限速，多连接实测无提升），
> 上传 QQ 约 0.5~0.8MB/s。想更快就压小体积：`BILI_VIDEO_HEIGHT=360`（画质换速度，
> 大约再快一倍）、或调低 `BILI_VIDEO_MAX_MB`。

> 20MB 上限只对短视频有意义。长视频（30 分钟）压到 150kbps 视频 + 64kbps 音频
> 也有 35MB，硬卡 20MB 会「永远发不出去」，所以超过某个时长后上限会自动放宽
> （按最低码率算，大约每分钟 +1.7MB）。短片的压缩参数一点不受影响。

配置（`.env`，改完 `docker compose up -d nonebot` 生效）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BILI_VIDEO_ENABLE` | 1 | 总开关 |
| `BILI_VIDEO_AUTO` | 1 | 群里不用@也响应（0=必须@） |
| `BILI_VIDEO_GROUPS` | 空 | 只在这些群自动响应（逗号分隔） |
| `BILI_VIDEO_CONFIRM` | 1 | 先回卡、等 y 确认（0=直接发） |
| `BILI_VIDEO_MAX_DURATION` | 1800 | 超过这个秒数不发（30 分钟） |
| `BILI_VIDEO_MAX_MB` | 20 | 480P 体积上限；长视频按最低码率自动放宽 |
| `BILI_VIDEO_HEIGHT` | 480 | 压缩后最大高度 |
| `BILI_VIDEO_CRF` | 30 | 压缩质量 |
| `BILI_VIDEO_QN` | 64 | 下载源画质（64=720P） |
| `BILI_VIDEO_TTL` | 600 | 确认有效期（秒） |

## 快速开始

```bash
# 0. 拉代码（舞萌直连的 SDK 是 submodule，必须一起拉）
git clone --recursive git@github.com:EricScottLin/Erechtheus.git
# 已有仓库更新时：git submodule update --init --recursive

# 1. 配置（.env 与 .env.prod 含密钥，已 gitignore，按模板填写）
cp .env.example .env
cp .env.prod.example .env.prod

# 2. 生成 LLBot 的 OB11 配置（会备份原配置）
bash scripts/gen-llbot-config.sh

# 3. 下载舞萌静态资源到 maimai-static/static（约 491M，不入 Git）
#    见 https://github.com/Yuri-YuzuChaN/nonebot-plugin-maimaidx

# 4. 启动
docker compose up -d

# 5. 查看日志
docker compose logs -f nonebot
```

## 目录结构

```
├── bot.py                     # NoneBot 入口，显式加载本地插件
├── pyproject.toml             # 依赖声明 + [tool.nonebot] 配置
├── Dockerfile.nonebot         # NoneBot 镜像构建
├── docker-compose.yml         # LLBot + NoneBot 编排
├── .env.example               # 环境变量模板（LLBot 侧）
├── .env.prod.example          # 环境变量模板（NoneBot 侧）
├── plugins/                   # 本地/vendored 插件
├── vendor/
│   └── MaimaiDXCHN/           # git submodule：舞萌直连的 SDK（只读挂到 /opt/maimai-dx-chn）
├── scripts/
│   ├── gen-llbot-config.sh    # 生成 LLBot OB11 配置（绑定回环+token）
│   └── verify-plugins.py      # 宿主机 venv 验证插件加载
├── docs/
│   ├── CURRENCY_SPEC.md       # 货币系统规范（双代币、防通胀、新增检查清单）
│   ├── FEATURES_2026-10-20.md # 功能更新记录（群管/表情/金句/按群开关）
│   ├── EVENTS.md              # 事件与埋点
│   └── FESTIVALS.md           # 节日/节气数据
└── legacy/
    └── hoshino-user-changes/  # HoshinoBot fork 中个人改动的存档
```

## 不入 Git 的内容

| 路径 | 原因 |
|---|---|
| `.env` / `.env.prod` | 含 Auth Token、WebUI Token、OB11 Access Token |
| `llbot-data/` | QQ 登录态 / session / `auth_token.txt` |
| `data/` | 插件运行时数据（签到记录、金币） |
| `maimaidx-static/` | 舞萌静态资源，约 491M |
| `static-src/` | 资源包下载源，约 446M |

模板文件 `.env.example` / `.env.prod.example` 已保留。

## 安全约定（硬性要求）

1. **LLBot WebUI(3081) 与 OneBot WS(3001) 绝不暴露公网**，只绑 `127.0.0.1`。
   远程管理 WebUI 用 SSH 隧道：`ssh -L 3081:127.0.0.1:3081 user@server`
2. 所有密钥只放 `.env` / `.env.prod`，不进 Git。
3. `ONEBOT_ACCESS_TOKEN` 两侧必须一致，用 `openssl rand -hex 32` 生成。
4. 验证公网不可达：`curl -m 4 http://<公网IP>:3081/` 应返回 `000`。

## 已知坑

| 现象 | 原因 | 解法 |
|---|---|---|
| 适配器日志 `does not support websocket client connections! Ignored` | `DRIVER=~fastapi` 单独使用只有 `ASGIMixin`，没有 `WebSocketClientMixin` | `DRIVER=~fastapi+~websockets` |
| 写了 `ONEBOT_WS_HOST/PORT` 却连不上 | 适配器没有这两个配置项 | 用 `ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]` |
| 端口 3081 暴露公网 | LLBot `startup.sh` 会把 `default_config.json` 的 `host` 改成 `""`；`LLBOT_QQ` 为空时回退到该文件 | 设 `LLBOT_QQ` 并运行 `scripts/gen-llbot-config.sh` 生成 `config_<QQ>.json` |
| maimaidx 报 `unable to open database file` | 插件把可变状态放 `static/data`，而 `/static` 挂成了 `ro` | 挂载必须 `:rw` |
| `SettingsError: error parsing env var "superusers"` | compose 的 `environment` 传了空串 `SUPERUSERS`，NoneBot 按 JSON 解析失败 | 不用时整行注释掉 |
| `docker compose up -d` 显示 Running 但没生效 | 未检测到变更 | `docker compose restart nonebot` |

## 备份与部署规范

服务器每 6 小时自动备份数据与密钥，详见 [docs/OPS_BACKUP.md](docs/OPS_BACKUP.md)。
手动备份：`cd /srv/qqbot && ./backup.sh`。

改动后的部署/验证/排障规范见 [docs/SKILL_qqbot-deploy.md](docs/SKILL_qqbot-deploy.md)（同内容也装在 DSH 技能目录，全会话生效）。

## 文档

- [功能更新记录（2026-10）](docs/FEATURES_2026-10-20.md) —— 群聊管理/表情包/金句/群精华/按群开关
- [货币系统规范](docs/CURRENCY_SPEC.md) —— 双代币设计、防通胀指标、新增代币检查清单
- [事件与埋点](docs/EVENTS.md)
- [节日与节气数据](docs/FESTIVALS.md)
- [NoneBot 官方文档](https://nonebot.dev/)

## 分支说明

- `main` —— 本项目（LLBot + NoneBot 部署）
- `hoshino` —— 历史分支：HoshinoBot fork（已停止维护）。
  其中个人改动已提取至 `legacy/hoshino-user-changes/`

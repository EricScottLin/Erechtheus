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
| `qqbot_toolbox` | 自研 | `/ping`（探活+链路延迟）、`/echo`、`/help`、`/status` |
| `qqbot_economy` | 自研 | 每日签到领 Kleos 荣耀、捕鱼赚 Drachma 德拉克马、鱼竿/鱼饵/抽奖 |
| `nonebot_plugin_bilibili` | vendored from [GreaterLG70/nonebot-plugin-bilibili](https://github.com/GreaterLG70/nonebot-plugin-bilibili) | @机器人+B站链接解析视频、`/bilibili rank` 榜单、`/bilibili up` 查 UP 主 |
| `nonebot_plugin_maimaidx` | [Yuri-YuzuChaN/nonebot-plugin-maimaidx](https://github.com/Yuri-YuzuChaN/nonebot-plugin-maimaidx) | 舞萌 DX 查分器 |

> **为什么 bilibili 插件是 vendored 的**：PyPI 上的 `nonebot-plugin-bilibili`
> 是 `mengbingnaixi` 的「动态订阅」插件，与本文需要的「视频解析」插件**同名不同物**，
> 直接 `pip install` 会装错。故将源码放入 `plugins/`，由 `bot.py` 用 `Path` 加载，
> 同时避开包名冲突。

## 快速开始

```bash
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
├── scripts/
│   ├── gen-llbot-config.sh    # 生成 LLBot OB11 配置（绑定回环+token）
│   └── verify-plugins.py      # 宿主机 venv 验证插件加载
├── docs/
│   ├── CURRENCY_SPEC.md       # 货币系统规范（双代币、防通胀、新增检查清单）
│   └── QQBOT_DEPLOYMENT_STATUS.md  # 部署状态与待办
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

## 文档

- [货币系统规范](docs/CURRENCY_SPEC.md) —— 双代币设计、防通胀指标、新增代币检查清单
- [部署状态与待办](docs/QQBOT_DEPLOYMENT_STATUS.md)
- [NoneBot 官方文档](https://nonebot.dev/)

## 分支说明

- `main` —— 本项目（LLBot + NoneBot 部署）
- `hoshino` —— 历史分支：HoshinoBot fork（已停止维护）。
  其中个人改动已提取至 `legacy/hoshino-user-changes/`

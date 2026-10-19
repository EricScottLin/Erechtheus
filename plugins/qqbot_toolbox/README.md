# qqbot_toolbox — QQBot 工具箱插件

NoneBot2 本地插件，提供消息链路验证与日常排查用的基础命令。

## 命令

| 命令 | 别名 | 说明 |
|---|---|---|
| `/ping` | — | 探活。测量**接口往返**（NoneBot↔LLBot）与**链路延迟**（QQ→LLBot→OneBot WS→NoneBot），并按延迟给拥堵评价 |
| `/echo <内容>` | — | 原样回显，验证「收到 → 处理 → 发出」全链路 |
| `/help` | `帮助`、`菜单` | 列出当前已加载的插件及其 matcher 数量 |
| `/status` | `状态` | 在线/健康状态、收发消息数、已运行时长 |

`/ping` 的两个指标：

- **链路延迟** = 消息事件的 `time` 字段到处理时刻的差值，
  覆盖 QQ 投递 → LLBot → OneBot 正向 WS → NoneBot 全链路
- **接口往返** = `get_status` API 调用的耗时，
  覆盖 NoneBot → LLBot → NoneBot

延迟分级：<50ms 🚀 畅通无阻 · <200ms ✅ 正常 · <500ms 🐌 有点堵 ·
<1000ms 😰 堵得慌 · 其他 🆘 雅典港大堵船

## 配置

配置项通过环境变量或 `.env` / `.env.prod` 注入，前缀 `qqbot_toolbox_`：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `qqbot_toolbox_at_sender` | bool | `true` | 回复时是否 @ 发起者 |
| `qqbot_toolbox_help_max` | int | `20` | `/help` 最多列出的插件数（1–100） |
| `qqbot_toolbox_status_timeout` | float | `5.0` | `/ping`、`/status` 调用 OneBot 接口的超时（秒） |

## 开发

按 NoneBot 官方插件编写规范开发：
<https://nonebot.dev/docs/tutorial/create-plugin>

结构为「包插件」（含 `__init__.py` 的目录）：

```
plugins/qqbot_toolbox/
├── __init__.py    # 插件元信息、事件响应器与处理函数
├── config.py      # pydantic 配置模型
└── README.md
```

本地验证加载（宿主机 venv）：

```bash
cd /srv/qqbot
.venv/bin/python -c "
import nonebot
nonebot.init()
from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
nonebot.get_driver().register_adapter(OneBotAdapter)
print(nonebot.load_plugin('plugins/qqbot_toolbox'))
"
```

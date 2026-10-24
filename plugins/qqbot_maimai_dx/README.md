# 舞萌 DX 直连插件（qqbot_maimai_dx）

把 [MaimaiDXCHN](https://github.com/EricScottLin/MaimaiDXCHN) 的 `BotController`
（平台无关指令处理器）与 `ChatBridge`（真实 IM 桥接层）接到 QQ，提供官方服资料/成绩读取、
查分器同步预览、成绩导出与独立登出。

和 `nonebot_plugin_maimaidx` 的分工：

| | `nonebot_plugin_maimaidx` | 本插件 |
|---|---|---|
| 数据来源 | 水鱼 / 落雪查分器（第三方归档） | 官方服（舞萌机台协议） |
| 绑定方式 | 水鱼 OAuth / 落雪密钥 | 微信「舞萌DX」小程序二维码（发图片即可） |
| 能做什么 | b50、牌子、排名、猜歌等 | 直读资料/成绩、生成待确认同步预览、导出、登出核验 |

## 可见范围（三层，任何一层不满足都不出声）

| 场景 | 舞萌绑定 | 其他指令 |
|---|---|---|
| `MAIMAI_DX_GROUP_IDS` 里的群 | 静默（必须私聊） | 可用（除「说明」外都要先绑定；大象信息未绑定时只给本地状态） |
| 其他群 | **完全静默** | **完全静默**（matcher 不匹配） |
| 私聊 | 「绑定」可用（需群成员 + 荣耀 Lv.10） | **静默**，不回「去某群用」以免暴露群 |

- 未绑定：一律提示「私聊我发『绑定』+ 二维码图片」。
- 菜单隐藏：`PluginMetadata.type = "library"` —— `picmenu-next` 与 `qqbot_toolbox`
  都只按这个字段隐藏插件，所以它不会出现在 `/help`、`/菜单`、`/指令` 里。
- 不注册任何 `qqbot_group` 功能键，`/开启 舞萌` 会直接回「没有这个功能」。

## 指令与价格（月币，按荣耀等级打折，失败不扣费）

| 指令 | 价格 | 说明 |
|---|---|---|
| 说明 | 免费 | 指令说明 |
| 大象信息 | 免费 | 玩家资料 + 本地状态（绑定/荣耀/月币/队列） |
| 更新成绩 | 10 | 读最新成绩（真实模式约 1 分钟） |
| 导出 | 10 | 导出 JSON + CSV |
| 绑定 | 100 | 首次绑定；同一个号重新登录免费（另需荣耀 Lv.10） |
| 登出 | 免费 | 安全阀：退出核验不该收费 |
| 识图 | 免费 | 成绩结算图 OCR：曲名/难度/达成率/判定，存本地档案 |
| 解绑 | 免费 | 仅管理员 |

指令名是短词，但匹配是**整条消息精确匹配**（允许一个前导 `/`）：所以「更新 了什么」
「绑定 手机号」这类日常闲聊不会误触发，也避开了 `/帮助`、`/状态`、`/资料` 与其他
插件的重名。

改价不用改代码：`.env.prod` 里写 `MAIMAI_DX_PRICES={"更新": 8}`。

## 识图（成绩结算图 → 本地档案）

群里发「识图」+ 结算图原图，本地 OCR（rapidocr，CPU 约 3~5 秒）识别：

- 曲名、难度（BASIC/ADVANCED/EXPERT/MASTER/Re:MASTER）、等级
- 达成率（取 4 位小数的百分比，忽略「我的最高」）
- 判定（CRITICAL PERFECT / PERFECT / GREAT / GOOD / MISS）与最大连击
- 结果存 `data/qqbot_maimai_dx/scores/<QQ>/<时间>.json`

**只读**：不写游戏、不写查分器，也不构造任何数据。斜拍/反光的照片会漏识别个别
判定数字，缺的字段显示 `?`，不会瞎猜。

## 绑定与解绑

- 私聊**直接发二维码图片**即可（`zxing-cpp` 解码），不用带文字。
- **一个 QQ 只能绑一个舞萌号**：绑定表存 `data/qqbot_maimai_dx/bindings.json`，
  记录 identity → 游戏 userId。换了另一张二维码只会提示"换号请先解绑"，
  **不会覆写**已绑定的账号；同一个号重新发码登录则正常刷新会话、且不再收费。
- 解绑（管理员 `解绑 @某人`）会同时清掉绑定表里的记录，之后可以绑别的号。
- 门槛：荣耀 **Lv.10**（累计 900 荣耀）+ 已 `/升阶`（Lv.9 是第一个觉醒点，不升阶卡在 9 级）
  + 本人是目标群成员（机器人会调 `get_group_member_info` 核实）。
- 绑定成功后**尽力撤回**那条二维码消息；二维码不落盘、不进日志、不进回复。
- 解绑：管理员在群里发「解绑 @某人」；自助解绑门槛默认满级 Lv.9999（等于只有管理员）。

## 配置（.env.prod）

```ini
MAIMAI_DX_MODE=demo                       # demo（完全离线）| real
MAIMAI_DX_GROUP_IDS=[984616276]           # 只在这些群可用
MAIMAI_DX_PROVIDER=lxns                   # lxns | diving-fish
MAIMAI_DX_PROBER_TOKEN=                    # 仅实传需要写这里
MAIMAI_DX_ALLOW_UPLOAD=false
MAIMAI_DX_VERSION=156
MAIMAI_DX_MIN_SESSION_SECONDS=65
MAIMAI_DX_IDENTITY_SALT=                   # 建议填随机串
MAIMAI_DX_PRICES={}                        # 覆盖默认价
MAIMAI_DX_BIND_MIN_LEVEL=10
MAIMAI_DX_SELF_UNBIND_LEVEL=9999
MAIMAI_DX_QR_MAX_BYTES=4000000
MAIMAI_DX_KEYCHIP_ID=                      # 留空用包内默认机台
MAIMAI_DX_CLIENT_ID=
MAIMAI_DX_REGION_ID=1
MAIMAI_DX_PLACE_ID=1403
```

## 依赖

`maimai-dx-chn` 以 Git submodule 固定在 `vendor/MaimaiDXCHN`，容器里只读挂到
`/opt/maimai-dx-chn` 并通过 `PYTHONPATH` 导入（私有仓库，不在镜像构建时 pip install）。
运行时另需 `pycryptodome`（机台加解密）、`zxing-cpp`（二维码解码）与
`rapidocr-onnxruntime`（成绩图 OCR），镜像已装，
`docker-entrypoint.sh` 会幂等补装。

## 已知边界

- 绑定关系落盘（`bindings.json`），重启不丢；成绩缓存仍在内存，重启后要重新「更新成绩」。
- 每次「更新成绩」会登录并登出，登出后这条二维码对应的登录凭证就作废，下一次操作需要
  重新私聊发一张新二维码（同一个号，免费）。
- 真实读取受服务端会话时序限制，登录后至少保留 `MAIMAI_DX_MIN_SESSION_SECONDS`（默认 65 秒）。
- 退出未确认时 `BotController` 会阻止再次读取与上传，先发「舞萌登出」核验。
- 其他群静默是有意设计；想在别的群开，改 `MAIMAI_DX_GROUP_IDS` 并重启。

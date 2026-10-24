# 舞萌 DX 直连插件（qqbot_maimai_dx）

把 [MaimaiDXCHN](https://github.com/EricScottLin/MaimaiDXCHN) 的 `BotController`
（平台无关指令处理器）与 `ChatBridge`（真实 IM 桥接层）接到 QQ，提供官方服资料/成绩读取、
查分器同步预览、成绩导出与独立登出。

和 `nonebot_plugin_maimaidx` 的分工：

| | `nonebot_plugin_maimaidx` | 本插件 |
|---|---|---|
| 数据来源 | 水鱼 / 落雪查分器（第三方归档） | 官方服（舞萌机台协议） |
| 绑定方式 | 水鱼 OAuth / 落雪密钥 | 微信「舞萌DX」小程序的二维码 |
| 能做什么 | b50、牌子、排名、猜歌等 | 直读资料/成绩、生成待确认同步预览、导出、登出核验 |

## 指令

```
舞萌帮助            看说明与当前模式
舞萌绑定 <二维码>   私聊绑定舞萌账号（只认证一次，不登录游戏）
舞萌资料            读取玩家资料（真实模式约 1 分钟）
舞萌成绩            读取最新成绩并生成同步预览（约 1 分钟）
舞萌预览            用已有缓存重新生成预览
舞萌上传            确认上传到查分器（需开启实传）
舞萌导出            导出成绩 JSON 与 CSV
舞萌状态            绑定、游戏会话与队列状态
舞萌登出            独立登出并核验远端状态
舞萌解绑            清空本 QQ 的绑定、缓存与预览
```

## 安全边界

- **默认 demo 模式**：`MAIMAI_DX_MODE=demo` 时走 `maimai_chn.bot.DemoSDK`，完全离线，
  不请求游戏服也不请求查分器。要在群里演示随便发，不会真的登录。
- **只处理私聊**：二维码、绑定、成绩、导出都是账号数据。群聊消息只回一句「请私聊」，
  不会进入 `BotController`。
- **只对 bot 主 + 白名单开放**：用的是自己的机台 keychip，不能让群友随意登录自己的号。
- **二维码不落日志**：`ChatBridge` 复用 `safe_message()`，日志里只留「绑定 [已隐藏]」；
  绑定成功后尽力撤回原消息。
- **查分器密钥只从环境变量注入**，不提供聊天命令输入密钥，也不回显。
- **实传默认关闭**：`MAIMAI_DX_ALLOW_UPLOAD=false` 时「舞萌上传」只会被拒绝；
  打开后仍需用户逐次发送「舞萌上传」确认。

## 配置（.env.prod）

```ini
MAIMAI_DX_MODE=demo                       # demo | real
MAIMAI_DX_ALLOW=["1943546421"]            # 白名单 QQ；bot 主始终允许
MAIMAI_DX_PRIVATE_ONLY=true
MAIMAI_DX_PROVIDER=lxns                   # lxns | diving-fish
MAIMAI_DX_PROBER_TOKEN=                    # 仅实传需要写这里
MAIMAI_DX_ALLOW_UPLOAD=false
MAIMAI_DX_VERSION=156
MAIMAI_DX_MIN_SESSION_SECONDS=65
MAIMAI_DX_IDENTITY_SALT=                   # 建议填随机串；改动会让已绑定会话失效
MAIMAI_DX_KEYCHIP_ID=                      # 留空用包内默认机台
MAIMAI_DX_CLIENT_ID=
MAIMAI_DX_REGION_ID=1
MAIMAI_DX_PLACE_ID=1403
```

## 依赖

`maimai-dx-chn` 以 Git submodule 固定在 `vendor/MaimaiDXCHN`，容器里只读挂到
`/opt/maimai-dx-chn` 并通过 `PYTHONPATH` 导入（私有仓库，不在镜像构建时 pip install，
避免把凭据写进镜像层）。更新版本：

```bash
git -C /srv/qqbot/vendor/MaimaiDXCHN fetch origin
git -C /srv/qqbot/vendor/MaimaiDXCHN checkout <commit>
cd /srv/qqbot && git add vendor/MaimaiDXCHN && git commit
docker compose restart nonebot
```

真实模式还需要 `pycryptodome`（镜像已装，`docker-entrypoint.sh` 会兜底补装）。

## 已知边界

- 绑定、成绩缓存、待确认预览都只在内存：**容器重启后要重新绑定**。
- 真实读取受服务端会话时序限制，登录后至少保留 `MAIMAI_DX_MIN_SESSION_SECONDS`（默认 65 秒）。
- 退出未确认时 `BotController` 会阻止再次读取与上传，先发「舞萌登出」核验。
- 群聊不可用是有意设计，不是功能缺陷。

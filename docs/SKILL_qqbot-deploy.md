---
name: qqbot-deploy
description: QQBot（NoneBot2 容器化机器人）改动后的部署、验证、备份与排障规范；避免「改了没生效 / 依赖被镜像覆盖 / 无提示」这类乌龙。
whenToUse: 在 /srv/qqbot（线上）或 /data/qqbot（快照）修改插件、配置、Dockerfile、docker-compose、bot.py 之后；用户反馈「机器人没反应/没提示/功能没生效」时；怀疑部署环境异常时。
---

# QQBot 部署与排障规范

## 0. 核心铁律

1. **改完必须验证生效**，不能只看代码通过。至少做到「容器内构造事件跑一遍」。
2. **每次部署前先备份**：`cd /srv/qqbot && ./backup.sh`。
3. **部署后必须确认连上**：日志出现 `Bot 3194243533 connected`。
4. **「没提示」先查三件事**：功能开关、容器是否被换镜像、日志有无 ERROR。不要先怀疑正则/代码。

## 1. 环境事实（先记住，别猜）

| 项 | 值 |
|---|---|
| 线上目录 | `/srv/qqbot`（容器挂载的就是它，**改别处不生效**） |
| 快照目录 | `/data/qqbot`（历史独立，无 remote，只本地提交） |
| 容器 | `qqbot-nonebot`（业务）+ `qqbot-llbot`（协议端） |
| 镜像 | `${COMPOSE_PROJECT_NAME}-nonebot:latest` = `qqbot-nonebot:latest` |
| 数据目录 | `/srv/qqbot/data/<plugin>`（`LOCALSTORE_USE_CWD=true`） |
| Bot QQ | `3194243533`，昵称 `Eρεχθεύς` |
| 测试据点群 | `984616276`（Erectheion神庙，用户自己的群，功能全开） |
| git 远端 | `git@github.com:EricScottLin/Erechtheus.git` |

## 2. 标准部署流程

```bash
cd /srv/qqbot
./backup.sh                                   # ① 先备份
# ② 改代码：在 /srv/qqbot 里改（或从工作区同步），同时同步 /data/qqbot 快照
docker compose restart nonebot                # ③ 重启
sleep 20
docker logs --since 2m qqbot-nonebot 2>&1 | grep -E "connected|ERROR|Failed to import"
# ④ 容器内构造事件实测（见 §4）
# ⑤ 测试 -> commit -> push（见 git-commit-push skill）
```

**只改 `plugins/` 下代码**：restart 即可。
**改了 `Dockerfile.nonebot` / `pyproject.toml` / 新增 pip 依赖**：必须 `docker compose up -d --build nonebot`，只 restart 不会装。

## 3. 依赖与镜像（踩过的坑，务必遵守）

- **pip 装在容器里的包不是持久的**：容器被重建（镜像是旧的/被覆盖）就会消失。
- `docker-compose.yml` 里 nonebot 服务已固定 `image: ${COMPOSE_PROJECT_NAME}-nonebot:latest`，**不要删掉这行**，否则会被其他项目同名的 `qqbot-nonebot:latest` 覆盖（2026-10-21 就是因为这个导致 nbnhhsh 丢失、整段时间功能不响应）。
- 新增 pip 依赖要**三处同步**：
  1. `Dockerfile.nonebot` 里 `RUN pip install ...`
  2. `docker-entrypoint.sh` 的启动自检（缺就补装，幂等）
  3. 立即在运行中的容器里装一次：`docker exec -i qqbot-nonebot pip install <pkg>`
- 装包注意 pillow 约束：`maimaidx` 要 `<12`、`rollpig` 要 `>=12.2`，统一按 `pillow<13` 装。
- 验证依赖真的在：`docker exec -i qqbot-nonebot python -c "import <mod>"`（注意此时 NoneBot 未 init，只能测 import 不测配置）。

## 4. 验证生效：容器内构造事件实测

不要只跑单元测试就宣布完成。用真实容器 + 假 Bot 跑一遍：

```bash
docker exec -i -w /app qqbot-nonebot python - <<'EOF'
import asyncio, nonebot
from pathlib import Path
nonebot.init()
from nonebot.adapters.onebot.v11 import Adapter, Bot as OBot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.message import handle_event
driver = nonebot.get_driver(); driver.register_adapter(Adapter)
ADAPTER = next(iter(driver._adapters.values()))
driver.config.superusers = {"1943546421"}
nonebot.load_plugin("nonebot_plugin_localstore")
nonebot.load_plugin(Path("plugins/qqbot_group"))      # 按需加载被测插件

class FakeBot(OBot):
    def __init__(self): super().__init__(ADAPTER, "3194243533"); self.calls=[]
    async def call_api(self, api, **data):
        self.calls.append((api, data))
        if api == "get_group_member_info": return {"role": "admin"}      # bot 是管理员
        if api == "get_group_member_list": return [{"user_id": 10002, "card": "张三", "nickname": "张三"}]
        return {"message_id": 1}

def ev(msg, uid=1943546421, gid=984616276, role="admin"):
    m = msg if isinstance(msg, Message) else Message(msg)
    return GroupMessageEvent(time=1, self_id="3194243533", post_type="message", sub_type="normal",
        user_id=uid, group_id=gid, message=m, original_message=m, message_type="group",
        raw_message=str(m), font=1, message_id=1, sender=Sender(user_id=uid, role=role))

async def main():
    bot = FakeBot(); await handle_event(bot, ev("极致睡眠"))
    print([(a, d.get("user_id"), d.get("duration")) for a, d in bot.calls if a == "set_group_ban"])
    print([d["message"].extract_plain_text() for a, d in bot.calls if a == "send_msg"])
asyncio.run(main())
EOF
```

要点：
- 日志过滤时**别只 grep emoji**（很多结果行是纯文本，会漏）。
- 数据类插件会把真实数据落盘，测试删除类操作前先备份，或临时改 `DATA_DIR` 到临时目录。

## 5. 用户说「没反应 / 没提示」的排障顺序

按顺序查，别跳步：

1. **功能开关**（最常见）
   ```bash
   cat /srv/qqbot/data/qqbot_group/features.json     # 看目标群该 key 是否 true
   ```
   默认关闭的功能（box / selfmute / greeting / work / roast / dian / meme_reply）只在 `984616276` 开着，其他群要 `/开启 <功能>`。
   命令本身有「功能没开」提示，**关键词类功能（selfmute/greeting/work/meme_reply）是完全静默的**——用户会以为坏了。
2. **容器是否被换镜像 / 依赖丢失**
   ```bash
   docker logs --since 30m qqbot-nonebot 2>&1 | grep -E "Failed to import|ModuleNotFoundError|Bot .* connected"
   docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
   ```
3. **消息到底有没有进来**
   ```bash
   docker logs --since 30m qqbot-nonebot 2>&1 | grep "message.group" | tail
   ```
   没有记录 = 协议端没推过来（群里没 @、被禁言、llbot 掉线），不是插件问题。
4. **插件加载顺序**：`bot.py` 里能决定依赖是否可用（如 `qqbot_core` 必须在 `qqbot_economy` 前）。
5. 最后才怀疑正则/逻辑，并用 §4 在容器内复现。

## 6. 改动的落地位置

同一份改动要落三处，缺一不可：

1. `/srv/qqbot`（线上，容器挂载）
2. `/data/qqbot`（快照仓库）
3. `/data/dsh/workspace/QQBot`（工作区，跑单测的地方，有时与 /srv 不同步）

多会话并行时，**只 `git add` 自己改的文件**，不要 `git add -A`，避免把别的会话半成品提交进去。

## 7. 备份（每次部署前）

```bash
cd /srv/qqbot && ./backup.sh          # 数据+密钥+git bundle，cron 每 6 小时也自动跑
```
明细见 `docs/OPS_BACKUP.md`。恢复到本地：`git clone git@github.com:EricScottLin/Erechtheus.git` 或从 `backups/git/*.bundle` clone。

## 8. 交付时必须报告

- 改了哪些文件、部署方式（restart / rebuild）
- 验证证据：容器内实测输出、`Bot ... connected` 时间、测试项数
- 若涉及功能开关：说明哪些群已开、哪些需要用户手动开
- commit hash + 分支 + push 结果（见 git-commit-push）

## 9. 历史乌龙清单（引以为戒）

| 时间 | 现象 | 根因 | 现在的防线 |
|---|---|---|---|
| 2026-10 | `/烟` 改了没效果 | 插件只放在工作区，容器挂的是 `/srv/qqbot` | 改动落三处（§6） |
| 2026-10 | `/撤` 报 TypeError | 漏 `await check_sender_admin(...)` | 单测覆盖每个命令 |
| 2026-10-21 | 群里说「极致睡眠」完全没反应 | ① 容器被同 tag 旧镜像替换，nbnhhsh 等依赖丢失 ② 该群功能未开启且静默 | §3 镜像固定 + entrypoint 自检；§5 排障顺序 |
| 2026-10-21 | 「估计要坐牢」被误禁言 | 关键词子串匹配 | 改整句/明确句式匹配，单测覆盖日常句子 |

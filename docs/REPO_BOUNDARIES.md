# 仓库职责边界

记录这套 bot 的代码该放在哪个仓库、改完要推哪里。目的是避免「改了一处、漏了另一处」，
以及避免同一个修复被提交两遍。

## 分工

| 仓库 | 角色 | 放什么 | 不放什么 |
|---|---|---|---|
| `Erechtheus`（`/srv/qqbot`） | 这台 bot 的**主控仓库** | `bot.py`、`docker-compose.yml`、Dockerfile、配置、文档、**所有定制插件**、`vendor/` 的 submodule 指针 | 可独立发布的 SDK 源码 |
| `MaimaiDXCHN` | 可独立复用的 **SDK** | `maimai_chn/`（协议、认证、会话、BotController、ChatBridge）、`tools/`、SDK 自己的测试与文档 | QQ 平台适配、荣耀/月币、任何 Erechtheus 专属业务 |
| 备份 | 整机快照 | `backups/` 下的数据与 git bundle，含快速重建所需的一切 | — |

## 什么放哪里

**SDK 改动（放 MaimaiDXCHN）**

- 协议编解码、密钥、HTTP 传输
- 二维码认证、登录/登出会话
- `BotController` 的指令语义、`ChatBridge` 的路由抽象
- 这些改动不能出现「荣耀」「月币」「qqbot_core」这类本机业务概念

**定制插件（放 Erechtheus）**

- 所有 `plugins/` 下的插件
- 包括 `qqbot_maimai_dx`：它虽然是 SDK 的适配器，但写满了荣耀等级门槛、月币计费、
  `qqbot_core` 资产来源，脱离这台服务器跑不起来，属于「没必要发布成独立仓库的小插件」
- 残留的 SDK 缺陷要下沉到 MaimaiDXCHN 再更新指针，不要在插件里绕过

## submodule 的正确操作

`vendor/MaimaiDXCHN` 在父仓库里**只是一个 commit hash**，这是最容易漏的地方。

改 SDK 时的完整流程：

```bash
# ① 在子仓库改并推送
cd vendor/MaimaiDXCHN
git checkout feat/chat-bridge        # submodule 默认是 detached HEAD，先切分支
#   ... 改代码、跑测试 ...
git add -A && git commit -m "fix: ..."
git push origin feat/chat-bridge

# ② 回到父仓库同步指针
cd ../..
./scripts/sync-vendor.sh --commit    # 自动切到远端最新并提交 gitlink
git push origin main
```

**漏掉第 ② 步的后果**：父仓库仍指向旧 commit，线上部署拉下来还是旧代码。
2026-11-24 的 `titlekey` 修复就是这样漏了一轮——子仓库提交了，父仓库指针没动，
线上扫码继续失败。

任何时候都可以用 `./scripts/sync-vendor.sh` 检查是否落后（只读，不改动）。

## 部署

```bash
cd /srv/qqbot
./scripts/sync-vendor.sh          # 先确认 submodule 与远端一致
./backup.sh
docker compose restart nonebot    # 只改插件/SDK：restart 即可
# 改了 Dockerfile / 依赖：docker compose up -d --build nonebot
```

`vendor/MaimaiDXCHN` 是**只读挂载**到容器的 `/opt/maimai-dx-chn`，改代码后必须
restart 容器才会重新加载，光改文件不生效。

## 判断口诀

> 这个改动，**别人拿到 MaimaiDXCHN 单独用**有没有意义？
>
> - 有 → 放 MaimaiDXCHN，走 submodule 流程
> - 没有（只有这台 bot 用得着）→ 放 Erechtheus，直接提交

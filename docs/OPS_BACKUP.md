# 运维：备份与代码持久化

## 一、自动备份（服务器上）

`/srv/qqbot/backup.sh` 负责一键备份，**每 6 小时自动跑一次**（cron: `/etc/cron.d/qqbot-backup`），保留最近 14 份。

```bash
cd /srv/qqbot && ./backup.sh        # 手动备份一次
ls backups/                          # 查看
```

产出：

| 路径 | 内容 |
|---|---|
| `backups/data/qqbot-backup-<时间>.tar.gz` | 数据快照：金币/签到/金句/表情/梗图/功能开关/打卡（约 17MB） |
| `backups/secrets/.env` `.env.prod` | 线上配置与密钥（权限 600） |
| `backups/git/Erechtheus-<日期>.bundle` | 完整 git 历史打包（每天一份，保留 7 份） |
| `backups/last-backup.txt` | 本次备份时间、分支、commit、未提交改动 |

排除项：`data/nonebot_plugin_htmlrender`（681MB 浏览器缓存，可重建）、`data/bili_video/job_*`（临时转码任务）、`maimaidx-static`。

## 二、恢复到本地（推荐做法）

### 方式 A：直接 clone GitHub（最省事）

```bash
git clone git@github.com:EricScottLin/Erechtheus.git QQBot
```

代码每次改动我都已 push，这是最新的。

### 方式 B：从 bundle 拿（含所有分支与历史，不依赖 GitHub）

把服务器上的 bundle 拉到本地：

```bash
# 在本地执行
scp root@<服务器>:/srv/qqbot/backups/git/Erechtheus-20261021.bundle .
git clone Erechtheus-20261021.bundle QQBot
cd QQBot && git remote -v     # 想继续用 GitHub 就再加 origin
git remote add origin git@github.com:EricScottLin/Erechtheus.git
```

bundle 是完整仓库（`git bundle verify` 显示 complete history），可以当普通仓库用。

### 方式 C：连数据一起拉

```bash
# 本地执行
scp -r root@<服务器>:/srv/qqbot/backups ./qqbot-backups
```

注意 `backups/secrets/` 里是真实密钥，别传到公共位置。

## 三、恢复数据（服务器出事时）

```bash
cd /srv/qqbot
docker compose stop nonebot
tar -xzf backups/data/qqbot-backup-<时间>.tar.gz   # 覆盖 data/
docker compose start nonebot
```

配置恢复：把 `backups/secrets/.env`、`.env.prod` 复制回 `/srv/qqbot/`。

## 四、代码仓库的持久化

| 位置 | 说明 |
|---|---|
| GitHub `EricScottLin/Erechtheus` | 唯一远端，每次改动都 push（`main`） |
| `/srv/qqbot/.git` | 线上仓库，部署目录 |
| `/data/qqbot` | 历史独立的开发快照仓库，仅本地提交 |
| `backups/git/*.bundle` | 每日打包的完整历史，可离线恢复 |

## 五、镜像依赖的坑（已修）

`docker-compose.yml` 里的 nonebot 服务现在用 `${COMPOSE_PROJECT_NAME}-nonebot:latest`，
不会再被其他同名 `qqbot-nonebot:latest` 的 compose 项目覆盖。
另外 `docker-entrypoint.sh` 会在启动前自检并补装 `nonebot-plugin-nbnhhsh` / `platformdirs`，
即使镜像重建也不会因为缺依赖启动失败。

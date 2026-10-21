#!/bin/bash
# ============================================================
#  QQBot 一键备份
#
#  1) 数据快照：./backups/data/qqbot-backup-<时间>.tar.gz
#     包含 data/ 下的关键状态（金币、签到、金句、表情、梗图、功能开关、打卡）
#     排除可重建的大件：htmlrender 浏览器缓存、bili 临时任务、舞萌静态资源
#  2) 密钥快照：./backups/secrets/（.env / .env.prod，权限 600）
#  3) 代码包：backups/git/Erechtheus-<日期>.bundle（完整 git 历史，可直接 clone）
#     —— 这是给你拉到本地用的，GitHub 之外的第二份代码备份
#
#  用法：./backup.sh [保留份数，默认 14]
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"
KEEP="${1:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BASE="backups"

mkdir -p "$BASE/data" "$BASE/secrets"

# --- 1. 数据快照 ---
INCLUDE=()
for d in qqbot_core qqbot_economy qqbot_group qqbot_repeat qqbot_meme qqbot_roast qqbot_pigverse; do
    [ -d "data/$d" ] && INCLUDE+=("data/$d")
done
[ -d data/bili_video ] && INCLUDE+=("data/bili_video")  # 只含群开关状态，很小

tar -czf "$BASE/data/qqbot-backup-$STAMP.tar.gz" \
    --exclude='data/bili_video/job_*' \
    --exclude='data/nonebot_plugin_htmlrender' \
    --exclude='__pycache__' \
    "${INCLUDE[@]}" 2>/dev/null

# --- 2. 密钥快照 ---
for f in .env .env.prod; do
    [ -f "$f" ] && cp "$f" "$BASE/secrets/$f"
done
chmod 600 "$BASE/secrets/"* 2>/dev/null || true

# --- 3. 代码 bundle（每天一份，保留 7 份）---
mkdir -p "$BASE/git"
DAY="$(date +%Y%m%d)"
if [ ! -f "$BASE/git/Erechtheus-$DAY.bundle" ]; then
    git bundle create "$BASE/git/Erechtheus-$DAY.bundle" --all >/dev/null 2>&1 || true
fi
ls -1t "$BASE/git"/*.bundle 2>/dev/null | tail -n +8 | xargs -r rm -f

# --- 4. 记录代码版本 ---
{
    echo "time: $STAMP"
    echo "branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
    echo "commit: $(git rev-parse HEAD 2>/dev/null)"
    echo "status:"
    git status --short 2>/dev/null || true
} > "$BASE/last-backup.txt"
cp "$BASE/last-backup.txt" "$BASE/secrets/last-backup.txt" 2>/dev/null || true

# --- 5. 清理旧备份 ---
ls -1t "$BASE/data"/qqbot-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

SIZE=$(du -h "$BASE/data/qqbot-backup-$STAMP.tar.gz" | cut -f1)
COUNT=$(ls -1 "$BASE/data"/qqbot-backup-*.tar.gz 2>/dev/null | wc -l)
echo "[backup] $STAMP 完成：数据 $SIZE，保留 $COUNT 份，代码 $(git rev-parse --short HEAD 2>/dev/null)"

#!/usr/bin/env bash
# 把 vendor/ 下的 submodule 同步到远端最新，并检查父仓库 gitlink 是否需要提交。
#
# 为什么需要这个脚本：
#   submodule 在父仓库里只是一个 commit hash。改完 SDK 后如果只提交了子仓库、
#   忘了在父仓库提交 gitlink，线上跑的就还是旧代码——2026-11-24 就是这样漏掉了
#   titlekey 修复。这个脚本把「检查 → 同步 → 验证」固化下来，避免靠记忆。
#
# 用法：
#   scripts/sync-vendor.sh            # 同步并报告；有变化时给出提交命令
#   scripts/sync-vendor.sh --commit   # 同步后自动在父仓库提交 gitlink 变更
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
AUTO_COMMIT=0
[ "${1:-}" = "--commit" ] && AUTO_COMMIT=1

# submodule 期望跟随的分支（子仓库 push 到这里）
BRANCH="${VENDOR_BRANCH:-feat/chat-bridge}"
OPENSSL_SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -i $HOME/.ssh/qqbot_deploy"

git submodule status | while read -r line; do
  state=${line:0:1}
  rest=${line:1}
  sha=$(echo "$rest" | awk '{print $1}')
  path=$(echo "$rest" | awk '{print $2}')
  name=$(basename "$path")
  echo "── $name ($path)"

  [ "$state" = "-" ] && { echo "   未初始化，先跑：git submodule update --init"; continue; }

  GIT_SSH_COMMAND="$OPENSSL_SSH" git -C "$path" fetch -q origin "$BRANCH"
  remote_sha=$(git -C "$path" rev-parse "origin/$BRANCH")
  echo "   当前 $sha"
  echo "   远端 $remote_sha"

  if [ "$sha" = "$remote_sha" ]; then
    echo "   ✓ 已是最新"
    continue
  fi

  # 子仓库里有未提交改动就别动它，交给人工判断
  if [ -n "$(git -C "$path" status --porcelain)" ]; then
    echo "   ✗ 子仓库有未提交改动，先处理（commit 或 stash）再同步"
    continue
  fi

  behind=$(git -C "$path" rev-list --count "$sha..$remote_sha")
  echo "   → 落后 $behind 个提交，同步中"
  GIT_SSH_COMMAND="$OPENSSL_SSH" git -C "$path" checkout -q "$remote_sha"
  echo "   ✓ 已切到 $remote_sha"

  if [ "$AUTO_COMMIT" = "1" ]; then
    git add "$path"
    git -c user.name=qqbot -c user.email=qqbot@local commit -q \
      -m "chore: 同步 $name 到 $remote_sha"
    echo "   ✓ 父仓库已提交 gitlink"
  else
    echo "   ⚠ 父仓库 gitlink 已变更，记得提交："
    echo "       git add $path && git commit -m 'chore: 同步 $name'"
  fi
done

#!/usr/bin/env bash
# jobs/deploy_pull.sh — 服务器一键部署：从**自建裸库**拉最新 + 按需重启容器（§9.834）
#
# 用法（服务器上）：bash /opt/marcus-platform/jobs/deploy_pull.sh
#   · 只从 `/opt/git/marcus-platform.git`（远程名 self ✓）拉 ✓ 走本机磁盘 ⇒ 秒级 ✓
#   · 快进合并（--ff-only ✓）：非快进直接报错退出 ✗（绝不覆盖现场改动 ✓）
#   · 按改动路径判定是否重启：backend/ app s/ config/ docker/ ⇒ 重启 worker+backend ✓
#     仅 jobs/*.py、docs/、scripts/ 等 ⇒ **无需重启** ✓（子进程任务下次运行即生效 ✓）
#   · 重启前检查是否有链在跑（daily_inputs_chain 等 ✓）⇒ 有则**拒绝重启** ✗（避免打断 ✗）
set -uo pipefail
cd /opt/marcus-platform || exit 2
BARE=${BARE:-/opt/git/marcus-platform.git}
BRANCH=${BRANCH:-main}

# 0) 工作区必须干净（只允许 data/ 下的未跟踪产物 ✓）
if [ -n "$(git status --porcelain | grep -vE '^\?\?')" ]; then
  echo "✗ 工作区有未提交改动 ⇒ 先处理（git stash / 提交）✓"; git status --short | grep -vE '^\?\?' | head -8; exit 3
fi

BEFORE=$(git rev-parse HEAD)
git fetch -q "$BARE" "$BRANCH" || { echo "✗ fetch 失败（裸库不可达？）$BARE"; exit 4; }
if ! git merge --ff-only -q FETCH_HEAD; then
  echo "✗ 非快进合并 ⇒ 需人工处理（历史分叉 ✓）"; exit 5
fi
AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then echo "✓ 已是最新：$AFTER"; exit 0; fi
echo "✓ 已更新：${BEFORE:0:8} → ${AFTER:0:8}"
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
echo "$CHANGED" | head -12 | sed 's/^/   · /'
echo "   （共 $(echo "$CHANGED" | wc -l) 个文件）"

# 1) 需重启判定
if echo "$CHANGED" | grep -qE '^(backend/|apps/|config/|docker/)'; then
  # 2) 有链在跑就别重启 ✗
  if docker exec marcus-worker sh -lc 'for p in /proc/[0-9]*; do c=$(tr "\0" " " < $p/cmdline 2>/dev/null); case "$c" in *daily_inputs_chain*|*build_concept_long*|*trend_confirm*) echo HIT; break;; esac; done' 2>/dev/null | grep -q HIT; then
    echo "⚠️ 检测到盘后链正在跑 ⇒ **本次不重启** ✗（代码已更新 ✓；链跑完再执行一次本脚本即可 ✓）"
    exit 0
  fi
  echo "⇒ 重启 worker + backend（backend/ apps/ config/ docker/ 有改动 ✓）"
  docker restart marcus-worker marcus-backend >/dev/null 2>&1
  sleep 8
  docker ps --format '{{.Names}} {{.Status}}' | head -3 | sed 's/^/   /'
else
  echo "⇒ 无需重启 ✓（无 backend/ apps/ config/ docker/ 改动；子进程任务下次运行即生效 ✓）"
fi

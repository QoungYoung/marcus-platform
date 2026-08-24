#!/usr/bin/env bash
# docker/dsh/entrypoint.sh — marcus-dsh 容器启动入口
#
# 背景：compose 的 dsh-data named volume 挂在 /root/.dsh 上，会遮蔽镜像内
# /root/.dsh/profiles/... 的插件文件（首启后，镜像重建的 COPY 无法更新 volume
# 内文件——2026-08-24 部署 ETF 日线工具时踩坑：工具注册日志一直不更新）。
#
# 办法：把仓库维护的插件与 patch 放在镜像内 /opt/dsh-plugins/（volume 外），
# 每次启动时覆盖同步到 volume，保证「镜像 = 插件唯一事实源」，
# 重建镜像 + 重启容器即可完成插件升级，无需手工 docker cp。
# 只同步仓库维护的内容（bridge / t-compaction / cordis.patch.yml），
# 不动 volume 里的运行时派生文件（cordis.yml、pnpm-lock、sessions 等）。
set -euo pipefail

PROFILE_DIR=/root/.dsh/profiles/service
PLUGINS_SRC=/opt/dsh-plugins

# 1) 同步 dsh-marcus-bridge 插件
if [ -d "$PLUGINS_SRC/dsh-dsh-marcus-bridge" ]; then
  mkdir -p "$PROFILE_DIR/node_modules/dsh-dsh-marcus-bridge"
  cp -a "$PLUGINS_SRC/dsh-dsh-marcus-bridge/." "$PROFILE_DIR/node_modules/dsh-dsh-marcus-bridge/"
  echo "[entrypoint] 已同步 dsh-dsh-marcus-bridge -> $PROFILE_DIR/node_modules/dsh-dsh-marcus-bridge"
fi

# 2) 同步 dsh-t-compaction 插件
if [ -d "$PLUGINS_SRC/dsh-t-compaction" ]; then
  mkdir -p "$PROFILE_DIR/node_modules/dsh-t-compaction"
  cp -a "$PLUGINS_SRC/dsh-t-compaction/." "$PROFILE_DIR/node_modules/dsh-t-compaction/"
  echo "[entrypoint] 已同步 dsh-t-compaction -> $PROFILE_DIR/node_modules/dsh-t-compaction"
fi

# 3) 同步 cordis.patch.yml（用户 patch）
if [ -f "$PLUGINS_SRC/cordis.patch.yml" ]; then
  cp -a "$PLUGINS_SRC/cordis.patch.yml" "$PROFILE_DIR/cordis.patch.yml"
  echo "[entrypoint] 已同步 cordis.patch.yml -> $PROFILE_DIR/cordis.patch.yml"
fi

# 4) exec 正式启动命令（替换当前进程，保证信号直达 dsh）
exec dsh --profile service
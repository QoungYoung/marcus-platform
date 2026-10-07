#!/bin/bash
# 回测专用：切换 dsh 桥的「工具允许清单」（账本 §9.750 ✓）
#   用法：bash jobs/bt_tools_allow.sh off   # 回测：0 工具（写空文件 ⇒ 只注册 0 个 ✓）
#         bash jobs/bt_tools_allow.sh all   # 生产/日常：删文件 ⇒ 全工具 ✓
# 为什么要这样 ✗：白名单原先是 **compose 只读挂载** ✗ ⇒ 一份文件同时决定生产与回测 ✗
#   ⇒ 生产会拿到"空清单" ⇒ 0 工具 ✗（用户点名必须避免 ✓）⇒ 已拆挂载 ✓，改由本脚本运行时控制 ✓
set -u
C="${DSH_CONTAINER:-marcus-dsh}"
case "${1:-}" in
  # ⚠️ 容器内 /root/.dsh 是**只读**挂载 ⇒ 容器内改不了该文件 ✗
  #    ⇒ 只能"挂不同的文件"切换：回测挂空文件 ✓、生产不挂 ✓（见 docker-compose.bt.yml ✓）
  off)  echo "[tools] 回测（0 工具）：用 override 起容器 ⇒"
        echo "        cd docker && docker compose -f docker-compose.yml -f docker-compose.bt.yml up -d marcus-dsh" ;;
  all)  echo "[tools] 生产（全工具）：用**基础** compose 起容器（不挂白名单）⇒"
        echo "        cd docker && docker compose -f docker-compose.yml up -d marcus-dsh" ;;
  *)    echo "用法: bash jobs/bt_tools_allow.sh off|all"; exit 2 ;;
esac
echo "[tools] 注意：桥在**启动时**读取 ⇒ 需重启容器生效：docker restart $C"

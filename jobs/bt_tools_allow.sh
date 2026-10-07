#!/bin/bash
# 回测专用：切换 dsh 桥的「工具允许清单」（账本 §9.750 ✓）
#   用法：bash jobs/bt_tools_allow.sh off   # 回测：0 工具（写空文件 ⇒ 只注册 0 个 ✓）
#         bash jobs/bt_tools_allow.sh all   # 生产/日常：删文件 ⇒ 全工具 ✓
# 为什么要这样 ✗：白名单原先是 **compose 只读挂载** ✗ ⇒ 一份文件同时决定生产与回测 ✗
#   ⇒ 生产会拿到"空清单" ⇒ 0 工具 ✗（用户点名必须避免 ✓）⇒ 已拆挂载 ✓，改由本脚本运行时控制 ✓
set -u
C="${DSH_CONTAINER:-marcus-dsh}"
case "${1:-}" in
  off)  docker exec "$C" sh -c 'mkdir -p /root/.dsh && : > /root/.dsh/bt_tools_allow.txt' \
          && echo "[tools] 已写空清单 ⇒ 重启后**只注册 0 个** ✓（回测 ✓）" ;;
  all)  docker exec "$C" sh -c 'rm -f /root/.dsh/bt_tools_allow.txt' \
          && echo "[tools] 已删除清单 ⇒ 重启后**全工具** ✓（生产/上线 ✓）" ;;
  *)    echo "用法: bash jobs/bt_tools_allow.sh off|all"; exit 2 ;;
esac
echo "[tools] 注意：桥在**启动时**读取 ⇒ 需重启容器生效：docker restart $C"

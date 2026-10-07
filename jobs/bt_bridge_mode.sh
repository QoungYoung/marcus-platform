#!/usr/bin/env bash
# 回测桥模式切换（账本 §9.721 ✓）
#
# 用户 2026-10-07：「能不能在回测里屏蔽工具，用查出来的数据取代」✓
#
# 做两件事（**都在修改后需要重启桥才生效** ✓）：
#   ① 工具允许清单 → 只留 **as-of API 能服务**的（其余屏蔽 ✗，避免未来函数）
#   ② 桥的 MARCUS_API_URL → 指向回测自己的 **as-of API**（数据全部"钉在回测当天" ✓）
#
# 用法：  bash jobs/bt_bridge_mode.sh status|on|off
#   on  ⇒ 进入回测模式（写清单 + 改 docker/.env + 提示重启）
#   off ⇒ 恢复（还原清单 + 还原生产 API 地址 + 提示重启）
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# ★ 该卷目录是 **root 所有** ✗（实测 Permission denied ✓）⇒ 一律**通过容器**读写 ✓
ALLOW_IN_CT=/root/.dsh/bt_tools_allow.txt
ENVF=docker/.env
BACKUP=".dsh-tmp/wolfbt/bridge_backup/bt_tools_allow.prod.txt"
PROD_API='http://backend:8000/api/v1'
BT_API='http://172.18.0.1:13284/api/v1'
BT_TOOLS='get_stock_quote
get_intraday_minute
get_portfolio_positions
get_market_state
list_t_conditions
list_t_ai_actions'

_cmd="${1:-status}"
case "$_cmd" in
  status)
    echo "[bt_bridge_mode] 清单 ⇒ $(docker exec marcus-dsh sh -lc "grep -cvE '^#|^$' $ALLOW_IN_CT" 2>/dev/null) 个工具"
    docker exec marcus-dsh sh -lc "grep -vE '^#|^$' $ALLOW_IN_CT" 2>/dev/null | sed 's/^/    /'
    echo "[bt_bridge_mode] docker/.env 里的 MARCUS_API_URL ⇒ $(grep -E '^MARCUS_API_URL=' "$ENVF" 2>/dev/null || echo '（未设，用 compose 默认 = 生产 ✗）')"
    echo "[bt_bridge_mode] 桥容器实际值 ⇒ $(docker exec marcus-dsh sh -lc 'echo $MARCUS_API_URL' 2>/dev/null)"
    ;;
  on)
    mkdir -p "$(dirname "$BACKUP")" 2>/dev/null
    [ -f "$BACKUP" ] || docker exec marcus-dsh sh -lc "cat $ALLOW_IN_CT" > "$BACKUP" 2>/dev/null
    printf '%s\n' "$BT_TOOLS" | docker exec -i marcus-dsh sh -lc "cat > $ALLOW_IN_CT"
    sed -i "s#^MARCUS_API_URL=.*#MARCUS_API_URL=$BT_API#" "$ENVF" 2>/dev/null
    grep -q '^MARCUS_API_URL=' "$ENVF" 2>/dev/null || printf 'MARCUS_API_URL=%s\n' "$BT_API" >> "$ENVF"
    echo "[bt_bridge_mode] ✅ 已切到回测模式（清单 $(printf '%s' "$BT_TOOLS" | wc -l) 个 ✓；API=$BT_API ✓）"
    # ★ 可达性预检（账本 §9.721 ✓）：as-of API 若仍只绑 127.0.0.1 ⇒ 容器连不上 ⇒ 开启后工具会全失败 ✗
    if docker exec marcus-dsh sh -lc "curl -sS -m 4 $BT_API/health >/dev/null 2>&1"; then
      echo "[bt_bridge_mode] ✓ 容器可达 as-of API（$BT_API）"
    else
      echo "[bt_bridge_mode] ⚠️ 容器**连不上** as-of API（$BT_API）—— 请先让启动脚本带 --host（默认 172.18.0.1 ✓）"
      echo "[bt_bridge_mode]    否则开启后 6 个工具会全部失败 ✗（比现状更吵，但**不会再读未来数据** ✓）"
    fi
    echo "[bt_bridge_mode] ⚠️ 需重启桥才生效： docker compose -f docker/docker-compose.yml up -d --force-recreate dsh"
    ;;
  off)
    if [ -f "$BACKUP" ]; then docker exec -i marcus-dsh sh -lc "cat > $ALLOW_IN_CT" < "$BACKUP"; else docker exec -i marcus-dsh sh -lc "cat > $ALLOW_IN_CT" < /dev/null; fi
    sed -i "s#^MARCUS_API_URL=.*#MARCUS_API_URL=$PROD_API#" "$ENVF" 2>/dev/null
    echo "[bt_bridge_mode] ✅ 已恢复（清单还原 ✓；API=$PROD_API ✓）"
    echo "[bt_bridge_mode] ⚠️ 需重启桥才生效"
    ;;
  *)
    echo "用法: bash jobs/bt_bridge_mode.sh status|on|off"; exit 2;;
esac

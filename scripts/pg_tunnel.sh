#!/usr/bin/env bash
# pg_tunnel.sh — 维护 localhost:18789 -> 线上 marcusrv:127.0.0.1:5432 的常驻通道
#
# 为什么需要它：.env 的 DATABASE_URL 指向 127.0.0.1:18789，这个端口是通往
# 线上 PG 的隧道。隧道一旦断掉，所有写 PG 的代码就连接超时，模型于是"退回写
# 本地 JSON 文件"——这正是历史上 agent 总写 data/*.json 的第一原因。
# 所以隧道不能靠人现场敲，必须常驻 + 自愈。
#
# 用法：pg_tunnel.sh {start|stop|status|restart}
# start 是幂等的：已通就直接返回 0，所以可以当看门狗每 5 分钟跑一次。

set -uo pipefail

HOST="${MARCUS_SSH_HOST:-marcusrv}"
LOCAL_PORT="${MARCUS_PG_LOCAL_PORT:-18789}"
REMOTE_PORT="${MARCUS_PG_REMOTE_PORT:-5432}"
PIDFILE="${HOME}/.dsh/pg-tunnel.pid"
LOGFILE="${HOME}/.dsh/pg-tunnel.log"

alive() {
    timeout 3 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/${LOCAL_PORT}" 2>/dev/null
}

cleanup_stale() {
    if [ -f "$PIDFILE" ]; then
        local pid; pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            kill -TERM "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
            sleep 0.3
        fi
        rm -f "$PIDFILE"
    fi
    # 兜底：占着端口但没 pidfile 的残留 ssh
    local stray
    stray="$(pgrep -f -- "-L ${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" 2>/dev/null || true)"
    [ -n "${stray}" ] && kill -TERM ${stray} 2>/dev/null || true
}

case "${1:-status}" in
  start)
    if alive; then
        echo "ok: 127.0.0.1:${LOCAL_PORT} 已通"
        exit 0
    fi
    cleanup_stale
    mkdir -p "$(dirname "${LOGFILE}")"
    setsid nohup bash -c '
      while true; do
        ssh -NT -o BatchMode=yes -o ConnectTimeout=8 \
            -o ExitOnForwardFailure=yes \
            -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
            -o ControlMaster=no -o ControlPath=none \
            -L '"${LOCAL_PORT}"':127.0.0.1:'"${REMOTE_PORT}"' '"${HOST}"'
        sleep 5
      done' >>"${LOGFILE}" 2>&1 &
    echo $! > "${PIDFILE}"
    for _ in $(seq 1 24); do
        if alive; then
            echo "ok: 隧道已建立 → 127.0.0.1:${LOCAL_PORT} ≈ ${HOST}:127.0.0.1:${REMOTE_PORT}"
            exit 0
        fi
        sleep 0.5
    done
    echo "FAIL: 隧道建立超时，看 ${LOGFILE}" >&2
    exit 1
    ;;
  stop)
    cleanup_stale
    echo "ok: 已停止"
    ;;
  restart)
    cleanup_stale
    exec "$0" start
    ;;
  status)
    if alive; then
        echo "up: 127.0.0.1:${LOCAL_PORT} 通"
        exit 0
    fi
    echo "down: 127.0.0.1:${LOCAL_PORT} 不通"
    exit 1
    ;;
  *)
    echo "用法: $0 {start|stop|status|restart}" >&2
    exit 2
    ;;
esac

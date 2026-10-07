#!/bin/bash
# 回测「重启到某日」一键脚本（账本 §9.743）
#
# 用户 2026-10-07：「能不能编写一个脚本把重启任务做了」✓
#
# 它把今晚手工做过的五件事固化下来 ✓：
#   ① 保活检查：LLM 转发器(13001 ✓) 与 回测监控台(8799 ✓) 必须在，且**看门狗都在** ✓
#      （今晚它们被"停臂"连带带走多次 ✗ ⇒ 转发器没了 ⇒ 唤醒全部超时 ✗ ⇒ AI 决策缺失 ✓）
#   ② **安全停臂** ✓：调 `jobs/bt_stop_arm.py`（按 /proc 认本臂子树 ✓，显式排除自身与祖先 ✓）
#      —— 绝不用 pgrep/pkill -f ✗（今晚因此自杀 6 次 ✓）
#   ③ **账户回滚** ✓：调 `jobs/bt_rollback_account.py`（回到"重跑起点前一日"收盘 ✓，含
#      highest_price 重算 ✓；清掉 >= 起点的 触发/条件/AI决策 ✓）
#   ④ 重跑 ✓：`RESUME_FROM=<起点>` 起 launcher ✓（不 reset 账户、复用沙箱 ✓）
#   ⑤ 复验 ✓：进程在／账户未被重置／打印跟进检查点 ✓
#
# 用法 ✓：
#   bash jobs/bt_restart_from.sh 20260116                # dry-run（只打印计划 ✓）
#   bash jobs/bt_restart_from.sh 20260116 --yes          # 真做
#   bash jobs/bt_restart_from.sh 20260116 --yes --tag t35d
#
# 注意 ✓：launcher 与转发器脚本位于 `.dsh-tmp/wolfbt/`（scratch ✓）；
#         若该目录被清，可用环境变量覆盖：LAUNCHER=… FWD=… FWD_WD=… DASH_WD=…
set -u

FROM="${1:-}"
shift || true
DO_IT=0
TAG="t35d"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) DO_IT=1 ;;
    --dry-run) DO_IT=0 ;;
    --tag) TAG="${2:-t35d}"; shift ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
  shift || true
done

if [ -z "$FROM" ] || [ ${#FROM} -ne 8 ]; then
  echo "用法: bash jobs/bt_restart_from.sh <YYYYMMDD 重跑起点> [--yes] [--tag t35d]"
  exit 2
fi

cd "$(dirname "$0")/.." || exit 1
ROOT="data/_bt_$TAG"
ACC="drab$TAG"
LAUNCHER="${LAUNCHER:-.dsh-tmp/wolfbt/_run_t35d_exec.sh}"
FWD="${FWD:-.dsh-tmp/wolfbt/llm_forward.py}"
FWD_WD="${FWD_WD:-.dsh-tmp/wolfbt/llm_forward_watchdog.sh}"
DASH_WD="${DASH_WD:-.dsh-tmp/wolfbt/dash_watchdog.sh}"
LOGDIR=".dsh-tmp/wolfbt/logs"
PY=".venv/bin/python"

echo "==== 回测重启:从 $FROM 起（tag=$TAG, root=$ROOT, account=$ACC）===="
echo "  [模式] $([ "$DO_IT" = "1" ] && echo '执行 --yes ✓' || echo 'dry-run（只打印 ✓；要真做加 --yes）')"

# ── ① 保活：转发器 + 监控台 + 两个看门狗 ─────────────────────────────
echo "── ① 保活检查 ──"
if ! ss -ltn 2>/dev/null | grep -q ':13001'; then
  echo "  :13001 不在 ⇒ $([ "$DO_IT" = 1 ] && echo '拉起' || echo '将拉起')"
  if [ "$DO_IT" = 1 ]; then
    setsid nohup "$PY" "$FWD" >> "$LOGDIR/llm_forward.log" 2>&1 < /dev/null &
    echo $! > .dsh-tmp/wolfbt/llm_forward.pid
    sleep 4
  fi
else
  echo "  :13001 在 ✓"
fi
if [ "$DO_IT" = 1 ] && ! ps -eo args 2>/dev/null | grep -q '[l]lm_forward_watchdog'; then
  setsid nohup bash "$FWD_WD" > "$LOGDIR/fwd_watchdog.out" 2>&1 < /dev/null &
  echo $! > .dsh-tmp/wolfbt/llm_forward_watchdog.pid
  echo "  已起转发器看门狗 ✓"
fi
if ! ss -ltn 2>/dev/null | grep -q ':8799'; then
  echo "  :8799（监控台）不在 ⇒ $([ "$DO_IT" = 1 ] && echo '拉起' || echo '将拉起')"
  if [ "$DO_IT" = 1 ]; then
    setsid nohup "$PY" jobs/bt_dashboard.py --root "$ROOT" --account "$ACC" \
      --log "$LOGDIR/size_run_$TAG.log" --port 8799 >> "$LOGDIR/dashboard_8799.out" 2>&1 < /dev/null &
    echo $! > .dsh-tmp/wolfbt/dashboard_8799.pid
    sleep 6
  fi
else
  echo "  :8799 在 ✓"
fi
if [ "$DO_IT" = 1 ] && ! ps -eo args 2>/dev/null | grep -q '[d]ash_watchdog'; then
  setsid nohup bash "$DASH_WD" > "$LOGDIR/dash_watchdog.out" 2>&1 < /dev/null &
  echo $! > .dsh-tmp/wolfbt/dash_watchdog.pid
  echo "  已起看板看门狗 ✓"
fi

# ── ② 安全停臂 ────────────────────────────────────────────────────
echo "── ② 停臂（只停本臂子树 ✓ 不用模式匹配 ✗）──"
"$PY" jobs/bt_stop_arm.py --root "$ROOT" $([ "$DO_IT" = 1 ] && echo --yes) || {
  echo "  ⚠️ 停臂返回非 0（可能有残留 ✓ 请人工核）"; }

# ── ③ 账户回滚到"起点前一日"收盘 ────────────────────────────────────
echo "── ③ 账户回滚 ──"
TO=""
for f in "$ROOT"/_summary/prod_2026*.json; do
  [ -f "$f" ] || continue
  d=$(basename "$f" | sed 's/^prod_//; s/\.json$//')
  [ "$d" \< "$FROM" ] || continue
  [ -z "$TO" ] || [ "$d" \> "$TO" ] && TO="$d"
done
if [ -z "$TO" ]; then
  echo "  ✗ 找不到 < $FROM 的已完成快照 ⇒ 无法回滚（该起点之前没有已完成的天？）"; exit 3
fi
echo "  回滚目标日（TO）⇒ $TO（= < $FROM 的最新已完成日 ✓）"
"$PY" jobs/bt_rollback_account.py --to "$TO" --from "$FROM" --account "$ACC" --root "$ROOT" \
  $([ "$DO_IT" = 1 ] && echo --yes) || { echo "  ✗ 回滚失败 ⇒ 已中止（未重跑 ✓）"; exit 4; }

# ── ④ 重跑 ────────────────────────────────────────────────────────
echo "── ④ 重跑（RESUME_FROM=$FROM）──"
if [ "$DO_IT" != 1 ]; then
  echo "  （dry-run ✓ 将执行: RESUME_FROM=$FROM bash $LAUNCHER ✓）"
  echo "==== 计划结束（未改动任何东西 ✓）===="
  exit 0
fi
STAMP=$(date +%H%M%S)
RESUME_FROM="$FROM" setsid nohup bash "$LAUNCHER" > "$LOGDIR/restart_${FROM}_$STAMP.out" 2>&1 < /dev/null &
sleep 45
echo "  ── 启动输出:"
grep -E "resume|T22|T23" "$LOGDIR/restart_${FROM}_$STAMP.out" 2>/dev/null | head -8 | sed 's/^/     /'

# ── ⑤ 复验 ────────────────────────────────────────────────────────
echo "── ⑤ 复验 ──"
ps -eo pid,etime,args 2>/dev/null | awk '/[b]t_days.py/ {print "  臂: " $0}' | head -1 | cut -c1-110
"$PY" - "$ACC" <<'PYEOF' 2>/dev/null | sed 's/^/  /'
import os, sys
import psycopg2, psycopg2.extras
acc = sys.argv[1]
cn = psycopg2.connect(os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading",
                      connect_timeout=8)
cn.set_session(readonly=True, autocommit=True)
c = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
c.execute("SELECT count(*) n FROM paper_trades WHERE account_id=%s", (acc,))
print("成交 ⇒ %s 笔" % c.fetchone()["n"])
c.execute("SELECT round(available_cash::numeric,2) n FROM paper_account_info WHERE account_id=%s", (acc,))
r = c.fetchone()
print("现金 ⇒ %s" % (r["n"] if r else "（无账户行 ✗）"))
c.execute("SELECT count(*) n FROM paper_positions WHERE account_id=%s", (acc,))
print("持仓 ⇒ %s 只" % c.fetchone()["n"])
PYEOF
echo "  ★ 跟进检查（跑进当天后应看到）:"
echo "     · 唤醒成功 > 0、失败 = 0     （13001 在 ✓）"
echo "     · 触发闸门 = ACTIVE|ALLOWED （不是 HALT|BLOCKED ✗）"
echo "     · 监控台 http://127.0.0.1:8799/ 进度行含【当日判定浪型】✓"
echo "==== 完成 ✓ ===="

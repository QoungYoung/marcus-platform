#!/usr/bin/env bash
# scripts/ab_iso_seq.sh — 三臂顺序跑（**输入隔离 + 指纹校验** 版，§9.835）
#   t45a 基线 ✓ / t45b 做T时段窗=0 ✓ / t45c 底仓 floor 豁免=0 ✓
#   窗口 20260601→20260709（与 c 臂同起点、含建仓期 ✓ ⇒ 腿量足够、开关有作用对象 ✓）
set -uo pipefail
R=/home/fengx/marcus-platform; LOGD="$R/.dsh-tmp/wolfbt/logs"
cd "$R"
run_arm () {
  local tag=$1 port=$2; shift 2
  echo "[seq] ==== 起 $tag $(date +%H:%M:%S) ===="
  bash "$R/scripts/ab_iso.sh" "$tag" "$port" 20260601 20260709 "$@" > "$LOGD/iso_${tag}.prep.log" 2>&1
  grep -E '^\[iso\]' "$LOGD/iso_${tag}.prep.log" | tail -3 | sed 's/^/   /'
  bash "$R/.dsh-tmp/wolfbt/_run_${tag}_exec.sh" > "$LOGD/size_run_${tag}.log" 2>&1 &
  sleep 60
  pid=$(python3 - "$tag" <<'PY'
import os,sys
t=sys.argv[1]
for d in os.listdir('/proc'):
    if not d.isdigit(): continue
    try: c=open('/proc/%s/cmdline'%d,'rb').read().decode('utf-8','replace').replace(chr(0),' ')
    except Exception: continue
    if ('_bt_%s'%t) in c and ('bt_days.py' in c or 'bt_prod_run.py' in c): print(d); break
PY
)
  if [ -n "$pid" ]; then echo "[seq] $tag driver pid=$pid env: $(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep -E '^(WOLF_TURNOVER_GUARD|WOLF_BASE_FLOOR_LOSS_EXEMPT|WOLF_MA5_EXIT)=' | tr '\n' ' ')"; fi
  for i in $(seq 1 240); do grep -q '完成：' "$LOGD/size_run_${tag}.log" 2>/dev/null && break; sleep 30; done
  echo "[seq] ==== $tag 结束 $(date +%H:%M:%S)（$(grep -o '完成：[0-9]* 天 / [0-9]* 条腿' "$LOGD/size_run_${tag}.log" | tail -1)）===="
}
run_arm t45a 13321
run_arm t45b 13322 "export WOLF_TURNOVER_GUARD='0'"
run_arm t45c 13323 "export WOLF_BASE_FLOOR_LOSS_EXEMPT='0'" "export WOLF_MA5_EXIT='0'"
echo "[seq] 三臂全部结束 ✓ $(date +%H:%M:%S)"
echo "[seq] ==== 输入指纹对照（三臂必须逐位相同 ✓）===="
for t in t45a t45b t45c; do echo "  --- $t ---"; sed 's/^/    /' "$R/data/_bt_iso/$t/fingerprint.txt" 2>/dev/null; done
echo "[seq] ==== 对照表 ===="
.venv/bin/python scripts/ab_compare.py t45a t45b t45c 2>&1 | head -40

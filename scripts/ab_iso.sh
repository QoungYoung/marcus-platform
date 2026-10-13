#!/usr/bin/env bash
# scripts/ab_iso.sh — A/B 单臂（**输入隔离 + 指纹校验** 版，§9.835）
#
# 为什么 ✓：上一轮 t44 三臂**顺序跑且共用输入**（data/_bt_year 种子池 + LLM 回放缓存
#   --agent-cache data/_bt_llm_year_t35d --agent-mode record ✗）⇒ 后跑的臂会看到
#   前一臂**写入/补录**过的输入 ⇒ 实测 t44b 与 t44c 数字**逐位相同** ✗、且与 t44a 系统性不同 ✗
#   ⇒ 无法判读单变量效应 ✗。本脚本把这两类共享输入**按臂隔离** ✓，并记录**输入指纹** ✓。
#
# 用法：bash scripts/ab_iso.sh <tag> <port> <start> <end> [单变量 export 行…]
#   例：bash scripts/ab_iso.sh t45a 13321 20260601 20260709
#       bash scripts/ab_iso.sh t45b 13322 20260601 20260709 "export WOLF_TURNOVER_GUARD='0'"
set -uo pipefail
TAG=$1; PORT=$2; START=$3; END=$4; shift 4
R=/home/fengx/marcus-platform
ISO="$R/data/_bt_iso/$TAG"
TMPL="$R/.dsh-tmp/wolfbt/_run_t44a_exec.sh"
OUT="$R/.dsh-tmp/wolfbt/_run_${TAG}_exec.sh"

mkdir -p "$R/data/_bt_iso" "$ISO"   # ★ 必须同时建本臂目录（先前漏了 ⇒ cp 目标父目录不存在 ⇒ 复制失败 ✗）
# ① 隔离：种子池 + LLM 回放缓存（各一份，逐位相同 ✓）
# 种子池 `data/_bt_year` **不复制**（实测：臂运行期间它**零改动** ✓ —— 2026-10-13 15:30 后条目数 0 ✓，
#   而当天三臂 16:00–18:49 都在跑 ✓ ⇒ 它是**只读输入** ✓）⇒ 用**指纹**保证三臂看到同一份 ✓
SEED_SRC="$R/data/_bt_year"
if [ ! -d "$ISO/_bt_llm_cache" ]; then
  echo "[iso] 复制 LLM 回放缓存 ⇒ $ISO/_bt_llm_cache（142M ✓）"
  cp -a "$R/data/_bt_llm_year_t35d" "$ISO/_bt_llm_cache" || { echo "[iso] ✗ 缓存复制失败"; exit 2; }
fi
# ② 指纹（跑前记录 ✓ 三臂必须一致 ✓）
{
  echo "tag=$TAG  at=$(date -Is)"
  echo -n "seed_tree_size_digest="; find "$SEED_SRC" -maxdepth 2 -printf '%P %s\n' | sort | md5sum | cut -d' ' -f1
  echo -n "window_seed_json_digest="; find "$SEED_SRC" -maxdepth 2 -name '*.json' -printf '%P\n' | sort | while read -r f; do d8=${f:0:8}; case "$d8" in 2026*) ;; *) continue;; esac; [ "$d8" \> "$START" ] || [ "$d8" = "$START" ] || continue; [ "$d8" \< "$END" ] || [ "$d8" = "$END" ] || continue; printf '%s ' "$f"; md5sum "$SEED_SRC/$f" | cut -d' ' -f1; done | md5sum | cut -d' ' -f1
  echo -n "llm_cache_files="; find "$ISO/_bt_llm_cache" -type f | wc -l
  echo -n "llm_cache_digest="; find "$ISO/_bt_llm_cache" -type f -printf '%P %s\n' | sort | md5sum | cut -d' ' -f1
  echo -n "pack_shared_digest="; find "$R/data/_bt_full/pack_shared" -maxdepth 1 -printf '%P %s\n' 2>/dev/null | sort | md5sum | cut -d' ' -f1
} > "$ISO/fingerprint.txt"
echo "[iso] 指纹 ⇒ $ISO/fingerprint.txt"; sed 's/^/    /' "$ISO/fingerprint.txt"
# ③ 生成臂脚本：模板 ⇒ 隔离路径 + TAG/PORT/窗口 + 单变量（**driver 之前** ✓）
python3 - "$TMPL" "$OUT" "$TAG" "$PORT" "$START" "$END" "$ISO" "$SEED_SRC" "$@" <<'PY'
import re, sys
tmpl, out, tag, port, start, end, iso, seed_src = sys.argv[1:9]
extra = sys.argv[9:]
s = open(tmpl, encoding='utf-8').read()
s, n1 = re.subn(r"TAG=[A-Za-z0-9_]+;\s*PORT=[0-9]+", "TAG=%s; PORT=%s" % (tag, port), s, count=1)
s, n2 = re.subn(r"START=[0-9]{8};\s*END=[0-9]{8}", "START=%s; END=%s" % (start, end), s, count=1)
s, n3 = re.subn(r'"\$R"/data/_bt_year', '"%s"' % seed_src, s, count=1)
s, n4 = re.subn(r'--agent-cache "data/_bt_llm_year_t35d"', '--agent-cache "%s/_bt_llm_cache"' % iso, s, count=1)
assert n1 and n2 and n3 and n4, (n1, n2, n3, n4)
if extra:
    m = re.search(r"^setsid nohup .*bt_days\.py|^setsid nohup .*bt_prod_run", s, re.M)
    assert m, 'driver 行未找到'
    s = s[:m.start()] + "# ★ A/B 单变量（driver 之前 ✓）\n" + "\n".join(extra) + "\n" + s[m.start():]
open(out, 'w', encoding='utf-8').write(s)
print('[iso] 臂脚本 ⇒ %s（seed=%s ✓ 只读共享，llm=%s/_bt_llm_cache ✓ 按臂隔离，单变量=%r ✓）' % (out, seed_src, iso, extra))
PY
chmod +x "$OUT"

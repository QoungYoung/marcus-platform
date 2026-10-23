# -*- coding: utf-8 -*-
"""regen_wave_state_ladder.py — 用**浪型链**重生成历史 wave_state（旧的 LLM 档位作废）。

范围（只动**当前代码真正读**的那三类，2026-10-15 用户拍板「切换 + 历史重新生成」）：
  ① data/wave_state.json                     ← live（daily_decision/wave_gate/t_monitor/position_tier… 都读它）
  ② data/wave_state_<YYYY-MM-DD>.json        ← 带日期回填件（60 个；daily_decision 在回测下优先读它）
  ③ data/_bt_*/<day>/wave_state.json         ← 回测快照（5011 个；build_position_filter / 回放读它）
**不动**：data/_bt_*/<day>/wave_state_<date>.json（26.7 万个历史归档副本；决策路径不读它们，
按需再单独重生成 —— 加 --include-archives 才纳入）。

口径：先跑一次 wave_ladder.walk() 得到**含单向锁/重定浪**的逐日档位，再按每个文件所在目录日
取「≤ 该日的最新档」（无未来函数 ✓）。同一批只算一次、写 N 份，所以 5000+ 文件也很快。

★ 2026-10-23：**文件与 PostgreSQL 必须同批同源** ✓ —— 每写一份文件就 `wave_pg.upsert()` 一次
（`--no-pg` 可关 ✗）。事故经过：10-21 那次 `wave_state_pg_backfill` 把 90 份「短数据窗口」算出的
旧口径（origin 2704.09 / wave1_top 3674.40）灌进了库 ✗，而本脚本只重算文件、不回写库 ✗ ⇒
读侧 `wave_store`（PG 优先 ✓）一直读旧锚点，磁盘文件却早已正确 —— 90 天永久不一致且不自愈 ✗。

安全：默认 **dry-run**（只打印计划）；加 --apply 才写盘，写前先把所有目标文件打成一个
data/_waveagent_backup/wave_states_<ts>.tar.gz（可直接解包还原 ✓），并逐份原子替换（tmp + os.replace）。

用法::
    python jobs/regen_wave_state_ladder.py                      # 看计划（dry-run）
    python jobs/regen_wave_state_ladder.py --apply              # 备份 + 重生成
    python jobs/regen_wave_state_ladder.py --apply --roots data/_bt_year
"""
import argparse
import bisect
import collections
import glob
import json
import os
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_ladder as W  # noqa: E402


def targets(roots):
    """→ [(path, day)]：每个回测根下的 <day>/wave_state.json。"""
    out = []
    for r in roots:
        for f in sorted(glob.glob(os.path.join(r, "*", "wave_state.json"))):
            out.append((f, os.path.basename(os.path.dirname(f))))
    return out


def root_files(include_archives=False):
    """★ **生产侧**目标（默认只动这些）：live + 带日期回填件 + wave_state_history/<sim8>.json。

    wave_state_history/ 是 wave_gate.py:101 按**模拟日 sim8** 读的那份历史（生产侧）；
    2026-10-15 首轮重建漏了它 ⇒ 这里补上（文件名 YYYYMMDD.json，day 直接取文件名 ✓）。
    _bt_* 下的 26.7 万归档副本只有显式 --include-archives（且需 --include-backtests）才动 ✓。
    """
    out = []
    live = os.path.join("data", "wave_state.json")
    if os.path.exists(live):
        out.append((live, None))
    for f in sorted(glob.glob(os.path.join("data", "wave_state_[0-9]*.json"))):
        out.append((f, os.path.basename(f).replace("wave_state_", "").replace(".json", "").replace("-", "")))
    for f in sorted(glob.glob(os.path.join("data", "wave_state_history", "[0-9]*.json"))):
        out.append((f, os.path.basename(f).replace(".json", "").replace("-", "")))
    if include_archives:
        for f in sorted(glob.glob(os.path.join("data", "_bt_*", "*", "wave_state_[0-9]*.json"))):
            out.append((f, os.path.basename(f).replace("wave_state_", "").replace(".json", "").replace("-", "")))
    return out


def _pg_upsert(ws):
    """regen → PG 双写（**fail-open** ✓：任何 DB 异常都只打印、绝不打断文件写入 ✓）。"""
    try:
        import wave_pg
        return bool(wave_pg.upsert(ws))
    except Exception as _e:
        print("  [pg] 跳过（%s）" % str(_e)[:80])
        return False


def _is_pg_target(f):
    """只有 `data/wave_state_history/*` 回写 PG ✓（live 由 `write_wave_state` 自己 upsert ✓）。

    **`data/wave_state_<date>.json` 不回写** ✗ —— 那批里含 2016–2024 的旧 dated 档，
    用户 2026-10-23 明确「不要这批老档进库」；PG 的口径就是 live + history（回填也是这么灌的 ✓）。
    """
    return os.path.basename(os.path.dirname(str(f).replace("\\", "/"))) == "wave_state_history"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--roots", default="", help="显式指定回测根（逗号分隔）")
    ap.add_argument("--include-backtests", action="store_true",
                    help="★ 动 _bt_* 回测沙箱（**默认不动**：2026-10-15 教训 —— 用户要的是重跑生产，")
    ap.add_argument("--include-archives", action="store_true",
                    help="连 _bt_*/<day>/wave_state_<日期>.json 归档副本（26.7 万份，需 --include-backtests）")
    ap.add_argument("--no-pg", action="store_true",
                    help="只写文件、**不回写** PostgreSQL（默认双写 ✓）。")
    ap.add_argument("--dated-days", type=int, default=0,
                    help="★ 额外**生成**最近 N 个交易日的 data/wave_state_<YYYY-MM-DD>.json（带日期回填件 ✓）。"
                    " 2026-10-20 用户拍板：8-10 月的 dated 只有 17 天（稀疏 ✗），而 daily_decision 在回测下**优先读 dated** ⇒"
                    " 需要补齐到每一天 ✓（会覆盖已有那批快照 ✓ 内容由同一个带锁 walk 生成、与 history 一致 ✓）。")
    ap.add_argument("--history-days", type=int, default=0,
                    help="★ 额外**生成**最近 N 个交易日的 data/wave_state_history/<sim8>.json（目录不存在会自动建 ✓）。"
                    " 为什么需要：wave_gate.py:101 的回测取档读这个目录，而 2026-10-15 首轮重建只更新**已存在**的文件 ⇒"
                    " 生产上该目录根本不存在 ✗ ⇒ 回测取档落空（2026-10-20 用户实测反馈 ✓）。不改带日期回填件（尊重「回测别回退」✓）。")
    args = ap.parse_args()

    roots = [x for x in args.roots.split(",") if x]
    if args.include_backtests and not roots:
        roots = sorted(glob.glob(os.path.join("data", "_bt_*")))
    pairs = targets(roots) + root_files(args.include_archives)

    rows = W.load_rows()
    if not rows:
        print("无指数数据 ⇒ 放弃（fail-open）")
        return 1
    print("指数数据：%s -> %s（%d 根）｜ 目标文件 %d 份（回测根 %d 个）" % (
        rows[0][0], rows[-1][0], len(rows), len(pairs), len(roots)))

    evs = W.walk(rows)                       # ★ 含单向锁/重定浪，只算一次
    by = [e["date"] for e in evs]
    print("逐日档位：%d 天（%s -> %s）" % (len(evs), by[0], by[-1]))

    # ★ 2026-10-20（用户实测反馈）：`wave_gate.py:101` 的回测取档读 data/wave_state_history/<sim8>.json，
    #   而 2026-10-15 首轮重建只更新**已存在**的文件 ⇒ 生产上该目录根本不存在 ✗ ⇒ 回测取档落空。
    #   --history-days N ⇒ **主动生成**最近 N 个交易日的 history 档（目录自动建 ✓）；
    #   **不动** data/wave_state_<date>.json 回填件（尊重用户「回测别回退」✓）。
    if args.history_days > 0:
        os.makedirs(os.path.join("data", "wave_state_history"), exist_ok=True)
        _days = by[-int(args.history_days):]
        for d8 in _days:
            pairs.append((os.path.join("data", "wave_state_history", "%s.json" % d8), d8))
        print("history 目标：最近 %d 个交易日（%s ~ %s）" % (len(_days), _days[0] if _days else "-", _days[-1] if _days else "-"))

    # ★ 2026-10-20（用户拍板「补」）：--dated-days N ⇒ 把**带日期回填件**也补齐到每一天 ✓
    #   （daily_decision 在回测下优先读它 ✗ 稀疏会取不到档 ✓；覆盖已有快照是用户明确同意的 ✓）
    if args.dated_days > 0:
        _d2 = by[-int(args.dated_days):]
        _have = {p for p, _ in pairs}
        _added = 0
        for d8 in _d2:
            p = os.path.join("data", "wave_state_%s-%s-%s.json" % (d8[:4], d8[4:6], d8[6:8]))
            if p in _have:
                continue
            pairs.append((p, d8))
            _added += 1
        print("dated 补目标：最近 %d 个交易日 ⇒ 新增 %d 份（其余已存在 ✓）" % (len(_d2), _added))

    def ev_for(day):
        if not day:
            return evs[-1]
        i = bisect.bisect_right(by, str(day)[:8]) - 1
        return evs[i] if i >= 0 else None

    plan, dist = [], collections.Counter()
    for f, day in pairs:
        ev = ev_for(day)
        if not ev:
            continue
        try:
            old = (json.load(open(f, encoding="utf-8")) or {}).get("operation") or "?"
        except Exception:
            old = "?"
        dist[(old, ev["operation"])] += 1
        plan.append((f, ev, old))

    print("\n旧 op → 新 op 分布（前 12）：")
    for (a, b), n in dist.most_common(12):
        print("  %-8s → %-8s %5d 份%s" % (a, b, n, "" if a == b else "   ← 改变"))
    changed = sum(n for (a, b), n in dist.items() if a != b)
    print("会改变 operation 的：%d/%d = %.1f%%" % (changed, len(plan), 100.0 * changed / max(len(plan), 1)))
    print("新档位分布：", dict(collections.Counter(ev["key"] for _, ev, _ in plan)))

    if not args.apply:
        print("\n（dry-run；加 --apply 才写盘）")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join("data", "_waveagent_backup")
    os.makedirs(bdir, exist_ok=True)
    tarp = os.path.join(bdir, "wave_states_%s.tar.gz" % ts)
    with tarfile.open(tarp, "w:gz") as tf:
        for f, _, _ in plan:
            if os.path.exists(f):
                tf.add(f, arcname=os.path.relpath(f, ROOT))
    print("\n备份：%s（%d 份，%.1f MB）" % (tarp, len(plan), os.path.getsize(tarp) / 1e6))

    n_ok, n_err, n_pg = 0, 0, 0
    for f, ev, _ in plan:
        try:
            ws = W.to_wave_state(ev)
            os.makedirs(os.path.dirname(f) or ".", exist_ok=True)
            tmp = f + ".tmp"
            json.dump(ws, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, f)
            n_ok += 1
            # ★ 2026-10-23 事故修：regen 原先**只写文件、不回写 PG** ✗ ⇒ 10-21 那次
            #   `wave_state_pg_backfill` 灌进库的旧口径（短数据窗口算的 origin/wave1_top）
            #   在库里留了 90 天 ✗，而读侧 `wave_store` **PG 优先** ✓ ⇒ 线上一直读旧锚点，
            #   磁盘文件却早已重算正确 —— 两边永久不一致且不会自愈 ✗。故这里补双写 ✓。
            #   范围只限 `data/wave_state_history/*` ✓（`--dated-days` 那批 2016–2024 老 dated
            #   档**不进库** ✗，见 `_is_pg_target` 的口径）。
            if not args.no_pg and _is_pg_target(f):
                n_pg += 1 if _pg_upsert(ws) else 0
        except Exception as _e:
            n_err += 1
            if n_err <= 3:
                print("  写失败 %s: %s" % (f, str(_e)[:80]))
    live = W.write_wave_state(ev=evs[-1])
    print("写入完成：文件 成功 %d / 失败 %d ｜ PG 双写 %d 行 ｜ live → %s"
          % (n_ok, n_err, n_pg, live))
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""quant_break_vwap.py —— 量化他的「**破分时黄线直接走**」（账本 §9.555）

**口径**：分时黄线 ＝ **当日累计 VWAP**（Σ成交额/Σ成交量 ✓）。
  · **触发**：09:35 起，**第一根** 5 分钟 bar 收盘 **< 累计 VWAP** ✓ ⇒ 「破黄线」✓
  · **动作**：**立即以该 bar 收盘卖出** ✓
  · **对照**：①持有到当日收盘 ②持有到破线后 T+5 ③持有到 T+10 ✓
  · 入场基准 ＝ **前一交易日收盘**（＝"我们已经持有"✓）

输出：触发率 + 各口径的均值/中位/左尾 + **"早走"相对"持有"的收益差（bp）**
用法：`.venv/bin/python jobs/quant_break_vwap.py [--from 20260302] [--to 20260428] [--limit 4000]`
"""
from __future__ import annotations
import glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    d0, d1, limit = "20260302", "20260428", 4000
    for i, a in enumerate(sys.argv):
        if a == "--from" and i + 1 < len(sys.argv): d0 = sys.argv[i + 1]
        if a == "--to" and i + 1 < len(sys.argv): d1 = sys.argv[i + 1]
        if a == "--limit" and i + 1 < len(sys.argv): limit = int(sys.argv[i + 1])
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    close_cache = {}

    def closes(ts):
        if ts not in close_cache:
            close_cache[ts] = {r[0]: float(r[1]) for r in
                               db.execute("SELECT trade_date, close FROM bars WHERE ts_code=?", (ts,))}
        return close_cache[ts]

    files = sorted(glob.glob(os.path.join(ROOT, "data/_bt_full/mins/*_5min_*.json")))
    _skip_n = [0]
    rows = []
    n_used = 0
    for p in files:
        base = os.path.basename(p)
        try:
            day = base.split("_5min_")[1][:8]
            if not (d0 <= day <= d1):
                continue
            sym6, ex = base.split("_5min_")[0].split("_")
            ts = sym6 + "." + ex
            j = json.load(open(p, encoding="utf-8"))
            bars = j.get("bars") or []
            if len(bars) < 8:
                continue
            n_used += 1
            if n_used > limit:
                break
            # bars 是**数组** [ts_code, time, open, high, low, close, vol, amount] 且**按时间倒序** ✗
            def _f(b, i, key):
                if isinstance(b, dict):
                    return float(b.get(key) or 0)
                return float(b[i] or 0) if len(b) > i else 0.0
            def _t(b):
                if isinstance(b, dict):
                    return str(b.get("time") or b.get("trade_time") or "")
                return str(b[1] if len(b) > 1 else "")
            seq = sorted(bars, key=_t)                      # ★ 改成正序 ✓
            cum_amt = cum_vol = 0.0
            hit = None
            for b in seq:
                t = _t(b)[-8:-3]
                c = _f(b, 5, "close")
                v = _f(b, 6, "vol")
                amt = _f(b, 7, "amount") or (c * v)
                cum_amt += amt; cum_vol += v
                vwap = (cum_amt / cum_vol) if cum_vol else 0
                if t and t >= "09:35" and vwap and c and c < vwap and hit is None:
                    hit = (t, c)
                    break
            if hit is None:
                continue
            cl = closes(ts)
            days = sorted(cl)
            if day not in days:
                continue
            i = days.index(day)
            prev = cl[days[i - 1]] if i > 0 else 0
            if prev <= 0:
                continue
            px = hit[1]
            nxt = lambda k: (cl[days[i + k]] if i + k < len(days) else None)
            r_rule = (px / prev - 1) * 100
            r_hold = (cl[day] / prev - 1) * 100
            r5 = (nxt(5) / prev - 1) * 100 if nxt(5) else None
            r10 = (nxt(10) / prev - 1) * 100 if nxt(10) else None
            rows.append((ts, day, hit[0], r_rule, r_hold, r5, r10))
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
    if not rows:
        print("  样本为空 ✗"); return 0
    print("  ── 「破分时黄线即走」量化（%s ~ %s ✓）──" % (d0, d1))
    print("  样本 ✓: %d 个 (标的,日)｜扫描文件 %d｜输入 %s 起" % (len(rows), n_used, d0))
    def agg(idx, name):
        v = [r[idx] for r in rows if r[idx] is not None]
        if not v:
            print("    %-22s 无数据" % name); return
        v.sort()
        print("    %-22s n=%5d｜均值 %+6.2f%%｜中位 %+6.2f%%｜左尾(5%%) %+6.2f%%" %
              (name, len(v), st.mean(v), st.median(v), v[int(len(v) * 0.05)]))
    agg(3, "破线即卖（相对前收）")
    agg(4, "持有到当日收盘")
    agg(5, "持有到 T+5")
    agg(6, "持有到 T+10")
    d_close = [r[4] - r[3] for r in rows]
    d5 = [r[5] - r[3] for r in rows if r[5] is not None]
    d10 = [r[6] - r[3] for r in rows if r[6] is not None]
    print("  ── 「早走」比「继续持有」多/少多少（bp ✓）──")
    for name, arr in (("vs 当日收盘", d_close), ("vs T+5", d5), ("vs T+10", d10)):
        if arr:
            arr.sort()
            print("    %-14s 均值 %+6.1f bp｜中位 %+6.1f bp｜左尾 %+7.1f bp" %
                  (name, st.mean(arr) * 100, st.median(arr) * 100, arr[int(len(arr) * 0.05)] * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())

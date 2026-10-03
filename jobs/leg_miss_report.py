# -*- coding: utf-8 -*-
"""leg_miss_report.py —— **低吸/急杀买腿「未命中」留痕**（账本 §9.465 ✓ 用户要求 ✓）

**为什么要有它** ✓（§9.464 定论 ✓）：
  · 实测：比亚迪 0304 有腿、价格也触到了前一日低点，但**没买** ✗
  · 逐根求值才发现：那一根**放量 2.39 倍**，不满足 254 的「**温和缩量 vol_ratio ≤ 0.9**」✓
  · ⇒ **只有成交才打日志** ✗ ⇒ "为什么没买"**完全看不到** ✗ ⇒ 本工具补上这段留痕 ✓

**只读、不改判据** ✓：读当天 `legs_switch.jsonl`（∪ `legs.jsonl` ✓）＋ 该标的 5 分钟 ＋ 前一日日线 ⇒
  逐腿输出：**① 离触发线多远 ② 触碰时量比 ③ 缺哪一条条件** ✗
**开关** ✓：`WOLF_LEG_MISS_REPORT`（**默认 1 = 开** ✓，按用户 2026-10-03 要求；置 0 关闭 ✓）

用法 ✓：`.venv/bin/python jobs/leg_miss_report.py --day 20260304 [--sandbox data/_bt_t35/20260304]`
      ⇒ 同时写 `leg_miss_report_<day>.jsonl` 到沙箱 ✓（便于事后检索 ✓）
"""
from __future__ import annotations

import argparse, json, os, sqlite3, statistics as st, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINS = os.path.join(REPO, "data", "_bt_full", "mins")
BARS = os.path.join(REPO, "data", "_bt_full", "bars.sqlite")


def enabled() -> bool:
    return str(os.getenv("WOLF_LEG_MISS_REPORT", "1")).strip().lower() in ("1", "true", "yes", "on")


def _prev_low(ts_code: str, day: str):
    """前一交易日最低价 ✓（只读 ✓）"""
    try:
        c = sqlite3.connect("file:%s?mode=ro" % BARS, uri=True, timeout=5)
        c.execute("PRAGMA temp_store=MEMORY")
        r = list(c.execute("SELECT low FROM bars WHERE ts_code=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1",
                           (ts_code, day)))
        c.close()
        return float(r[0][0]) if r else None
    except Exception:
        return None


def _index_dump_hits(day: str) -> int:
    """上证当天「单根 5 分钟跌 ≥0.4%」的根数 ✓（253 判别用 ✓）"""
    p = os.path.join(MINS, "000001_SH_5min_%s.json" % day)
    if not os.path.exists(p):
        return -1
    try:
        b = sorted(json.load(open(p, encoding="utf-8"))["bars"], key=lambda r: str(r[1]))
    except Exception:
        return -1
    n = 0
    for i in range(1, len(b)):
        c0, c1 = float(b[i - 1][5]), float(b[i][5])
        if c0 > 0 and (c1 / c0 - 1) * 100 <= -0.4:
            n += 1
    return n


def _to_ts(sym: str) -> str:
    s = str(sym).strip().upper()
    return ("%s.%s" % (s[2:], s[:2])) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s


def report(day: str, sandbox: str) -> list:
    legs = []
    for fn in ("legs_switch.jsonl", "legs.jsonl"):
        p = os.path.join(sandbox, fn)
        if os.path.exists(p):
            for ln in open(p, encoding="utf-8"):
                ln = ln.strip()
                if ln:
                    try:
                        legs.append(json.loads(ln))
                    except Exception:
                        pass
    idx_hits = _index_dump_hits(day)
    rows = []
    for lg in legs:
        sym = str(lg.get("symbol") or lg.get("code") or "")
        if not sym:
            continue
        ts = _to_ts(sym)
        k6 = ts.split(".")[0]
        pf = os.path.join(MINS, "%s_%s_5min_%s.json" % (k6, ts.split(".")[-1], day))
        if not os.path.exists(pf):
            rows.append({"symbol": sym, "day": day, "verdict": "无分钟数据 ✗"})
            continue
        try:
            b = sorted(json.load(open(pf, encoding="utf-8"))["bars"], key=lambda r: str(r[1]))
        except Exception:
            continue
        pl = _prev_low(ts, day)
        vols = [float(x[6]) for x in b]
        vmed = st.median(vols) if vols else 0
        touched = []
        for x in b:
            if pl and float(x[4]) <= pl:
                touched.append(((str(x[1]).split(" ")[-1] or "")[:5], float(x[4]), float(x[5]), float(x[6]),
                                (float(x[6]) / vmed) if vmed else 0))
        ents = []
        for t, lo, cl, v, rel in touched:
            miss = []
            if not (0 < rel <= 0.9):
                miss.append("vol_ratio=%.2f 不在 (0, 0.9]（要求**温和缩量** ✓）" % rel)
            ents.append({"t": t, "low": round(lo, 2), "vol_ratio": round(rel, 2), "missing": miss})
        verdict = "触及未命中 ✗" if touched else "**全天未触及触发线** ✗"
        rows.append({"symbol": sym, "day": day, "kind": lg.get("stage"), "type": lg.get("type"),
                     "prev_low": (round(pl, 2) if pl else None),
                     "min_low": (round(min(float(x[4]) for x in b), 2) if b else None),
                     "touched": bool(touched), "verdict": verdict, "touch": ents[:4],
                     "index_dump_hits": idx_hits})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not enabled():
        print("  [leg_miss] 关（WOLF_LEG_MISS_REPORT=0）⇒ 跳过 ✓")
        return 0
    sb = a.sandbox or os.path.join(REPO, "data", "_bt_t35", a.day)
    rows = report(a.day, sb)
    print("  ── %s 买腿「未命中」留痕 ✓（共 %d 条腿 ✓）──" % (a.day, len(rows)))
    for r in rows:
        if r.get("touched"):
            _t = r["touch"][0]
            print("    %-9s ⇒ 触线 %s ✓｜线 %.2f｜该根量比 **%.2f** %s"
                  % (r["symbol"], _t["t"], r["prev_low"], _t["vol_ratio"],
                     ("✗ " + "；".join(_t["missing"])) if _t["missing"] else "✓ 条件满足 ⇒ 应成交 ✓"))
        else:
            print("    %-9s ⇒ 最低 %.2f vs 线 %s ⇒ **未触及** ✗（离线 %.2f%%）"
                  % (r["symbol"], (r.get("min_low") or 0), r.get("prev_low"),
                     ((r.get("min_low") or 0) / r["prev_low"] - 1) * 100 if r.get("prev_low") else 0))
    print("    指数急杀根数(253 用) ✓: %s" % (rows[0].get("index_dump_hits") if rows else "-"))
    out = a.out or os.path.join(sb, "leg_miss_report_%s.jsonl" % a.day)
    try:
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("    ✓ 已写 %s ✓" % out)
    except Exception as e:
        print("    ✗ 写盘失败 %s" % str(e)[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""eval_exit_rules.py — 阶段 1：兑现口径**变体对照**（离线，无生产改动）。

背景（docs/plan-exit-alignment.md §5 阶段 1）：阶段 0 把尺子换成"生产实际离场口径"后，
现在要在**同一批腿**上对照几种兑现规则变体：

  · 基线（现状）    = 生产实际已实现离场（paper_trades 的卖出腿，含费）
  · A 持 T+5 收盘   = 老尺子（对照用，不作验收）
  · V1 +3% 目标幅度 = 狼大「最低套利也是 3 个点」2026-08-04 /「T+0 2 个点我就够了」2025-04-03
  · V2 破黄线离场   = 狼大 2026-08-04 10:48「日均线那条黄线一旦突发跌破直接走」
                     ⚠️ 口径代理：分时黄线（分时均价线）无历史分钟数据 → 用**当日 VWAP 代理**
                     （amount/vol），判据 = 持仓期内某日 **最低价 < 当日 VWAP** → 当日收盘离场
  · V3 = V1 + V2（先到先执行）
  · V4 做 T 前置    = 只做"买入时已有底仓"的腿（狼大 2026-08-25「我今天没抄底 没有资格 T」）
  · V5 高低位分类型  = 高位腿（入场时标的 60 日价格分位 ≥70%）用 V3；低位腿持有到 T+5
                     （狼大 2026-09-04 15:07「高位方向…拉升后都走；低位方向…不动」）

指标：n / 胜率 / 均值 / 中位 / 盈亏比 / 最大单笔亏损 / **块状 t（按周）** / H1-H2 分段 /
      以及**同主题同日基线超额**（复用 eval_leg_metrics 的口径）。

口径纪律：n<100 只作探索；重叠样本看块状 t；**结论只用于决定是否接线**，不直接改生产。

用法：.venv/bin/python jobs/eval_exit_rules.py [--accounts stock] [--replay]
输出：<cache>/eval_exit_rules.json（默认 .dsh-tmp/wolfbt/legmetrics/）+ 控制台 markdown
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import math
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
import eval_leg_metrics as M  # noqa: E402

CACHE = M.CACHE
OUT = os.path.join(CACHE, "eval_exit_rules.json")


# ── 带 vol 的日线（VWAP 代理需要）────────────────────────────────────────
class BarsV(M.Bars):
    """在 eval_leg_metrics.Bars 之上补 vol：(date, o, h, l, c, amount, vol)。"""

    def __init__(self):
        super().__init__()
        self._v = {}

    def _load_csv_v(self):
        if getattr(self, "_csvv", None) is not None:
            return
        self._csvv = {}
        p = os.path.join(M.DATA, "bars_2026.csv.gz")
        if not os.path.exists(p):
            return
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if len(row) < 10 or not row[0] or row[1] == "trade_date":
                    continue
                try:
                    self._csvv.setdefault(row[0], {})[row[1]] = float(row[8] or 0)
                except (ValueError, IndexError):
                    continue

    def vol_of(self, sym, date8):
        sym = M.norm_symbol(sym)
        self._load_csv_v()
        v = (getattr(self, "_csvv", {}) or {}).get(sym, {}).get(date8)
        if v:
            return v
        # ETF：relay 缓存里有 vol（第 6 列 index 5 = amount，vol 未存）→ 用 amount/close 估
        try:
            import json as _j
            p = os.path.join(CACHE, "etf_daily.json")
            if os.path.exists(p):
                rows = (_j.load(open(p, encoding="utf-8")) or {}).get(sym) or []
                for r in rows:
                    if len(r) >= 6 and str(r[0]) == date8:
                        amt = float(r[5] or 0)
                        return amt  # 只有 amount；VWAP 用 amount 与 close 近似时单独处理
        except Exception:
            pass
        return 0.0


def _load_etf_bars():
    """ETF 观测缓存（relay fund_daily，含 open/high/low/close/amount）→ {sym: {date: bar}}。"""
    p = os.path.join(CACHE, "etf_daily.json")
    out = {}
    if not os.path.exists(p):
        return out
    try:
        d = json.load(open(p, encoding="utf-8")) or {}
        for sym, rows in d.items():
            for r in rows or []:
                if len(r) >= 6:
                    out.setdefault(M.norm_symbol(sym), {})[str(r[0])] = {
                        "open": float(r[1] or 0), "high": float(r[2] or 0), "low": float(r[3] or 0),
                        "close": float(r[4] or 0), "amount": float(r[5] or 0),
                        "vol": float(r[6]) if len(r) > 6 else 0.0}
    except Exception:
        pass
    return out


_ETF = _load_etf_bars()
_STOCK_V = {}


def _stock_bar_with_vol(sym, date8):
    """股票日线（bars_2026.csv.gz）→ 含 vol 的 bar；无则 None。"""
    global _STOCK_V
    if not _STOCK_V:
        p = os.path.join(M.DATA, "bars_2026.csv.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) < 10 or not row[0] or row[1] == "trade_date":
                        continue
                    try:
                        _STOCK_V.setdefault(row[0], {})[row[1]] = {
                            "open": float(row[2]), "high": float(row[3]), "low": float(row[4]),
                            "close": float(row[5]), "amount": float(row[9]), "vol": float(row[8] or 0)}
                    except (ValueError, IndexError):
                        continue
    return (_STOCK_V.get(M.norm_symbol(sym)) or {}).get(date8)


def bar_with_vol(sym, date8):
    b = _stock_bar_with_vol(sym, date8)
    if b:
        return b
    return (_ETF.get(M.norm_symbol(sym)) or {}).get(date8)


def vwap_of(sym, date8):
    """当日 VWAP 代理：股票用 amount/(vol×100)（amount 千元、vol 手）；ETF 用 amount/vol 不可得 → None。"""
    b = bar_with_vol(sym, date8)
    if not b:
        return None
    amt, vol = float(b.get("amount") or 0), float(b.get("vol") or 0)
    if amt > 0 and vol > 0:
        # 股票：amount 千元 / vol 手 → 元/股；ETF(fund_daily)：amount 千元 / vol 份
        v = amt * 1000.0 / (vol * 100.0) if not str(sym).startswith(("5", "1")) else amt * 1000.0 / vol
        v = amt * 1000.0 / (vol * 100.0)
        return v if v > 0 else None
    return None


# ── 变体模拟 ────────────────────────────────────────────────────────────
def sim_variant(bars, sym, entry_date, entry_px, variant, hold=5, tp=3.0):
    """返回 (收益率%, 出场方式)。V1/V3 用 tp；V2/V3 用"当日最低 < 当日 VWAP → 收盘离场"代理。"""
    if not entry_px:
        return None, "no_entry"
    rows = bars.get(sym)
    days = [r[0] for r in rows]
    i = M.snap_idx(bars, sym, entry_date)
    if i is None:
        return None, "no_bars"
    for k in range(1, hold + 1):
        if i + k >= len(rows):
            break
        d8 = rows[i + k][0]
        c = rows[i + k][4]
        if variant in ("V1", "V3") and (c / entry_px - 1.0) * 100.0 >= tp:
            return (c / entry_px - 1.0) * 100.0, "tp3"
        if variant in ("V2", "V3"):
            vw = vwap_of(sym, d8)
            lo = rows[i + k][3]
            if vw and lo < vw:
                return (c / entry_px - 1.0) * 100.0, "vwap_break"
    j = min(i + hold, len(rows) - 1)
    return (rows[j][4] / entry_px - 1.0) * 100.0, "hold_t5"


def pos_percentile(bars, sym, entry_date, win=60):
    """入场时标的在近 win 日的价格分位（高位/低位分型代理，V5 用）。"""
    rows = bars.get(sym)
    days = [r[0] for r in rows]
    i = M.snap_idx(bars, sym, entry_date)
    if i is None or i < 5:
        return None
    seg = [r[4] for r in rows[max(0, i - win + 1):i + 1]]
    lo, hi = min(seg), max(seg)
    return (rows[i][4] - lo) / (hi - lo) if hi > lo else None


def bucket_rows(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0}
    m = sum(v) / len(v)
    sd = st.pstdev(v) if len(v) > 1 else 0.0
    return {"n": len(v), "mean": round(m, 2), "median": round(st.median(v), 2),
            "win": round(sum(1 for x in v if x > 0) / len(v), 4),
            "t": round(m / (sd / math.sqrt(len(v))), 2) if sd > 0 else None,
            "worst": round(min(v), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="stock", help="生产腿账户（默认 stock；t=测试账户）")
    ap.add_argument("--replay", action="store_true", help="同时跑 428 参考腿（只有 V1/A）")
    args = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    rows, meta = M.fetch_paper_trades()
    legs, opens, adds, diag = M.build_legs(rows)
    bars, themes = M.Bars(), M.Themes()
    accts = tuple(a.strip() for a in args.accounts.split(",") if a.strip())
    strat = [l for l in legs if l["account"] in accts and l["entry_px"] and l["realized_pct"] is not None
             and abs(M._num(l.get("db_profit"))) > 1e-9]

    need = sorted({M.norm_symbol(l["symbol"]) for l in strat})
    miss = [s for s in need if len(bars.get(s)) < 30]
    if miss:
        bars.fetch_missing(miss)

    out = {"_meta": {**meta, "_accounts": list(accts), "_n_legs": len(strat),
                     "_spec": "jobs/eval_exit_rules.py（阶段 1 变体对照）"}, "variants": {}, "legs": []}
    variants = ("A_hold_t5", "V1_tp3", "V2_vwap", "V3_tp3_vwap", "V4_precond", "V5_bypos")
    KEYMAP = {"A_hold_t5": "A_hold_t5", "V1_tp3": "V1", "V2_vwap": "V2", "V3_tp3_vwap": "V3",
              "V4_precond": "V4_precond", "V5_bypos": "V5_bypos"}
    vals = collections.defaultdict(list)
    for l in strat:
        base = l["db_pct"] if l["db_pct"] is not None else l["realized_pct"]
        pos = pos_percentile(bars, l["symbol"], l["entry_date"])
        rec = {"symbol": l["symbol"], "account": l["account"], "entry_date": l["entry_date"],
               "exit_date": l["exit_date"], "prod": base, "pos_pct": pos,
               "had_position": bool(l.get("had_position")), "realized_pct": l["realized_pct"]}
        for v in ("V1", "V2", "V3"):
            r, how = sim_variant(bars, l["symbol"], l["entry_date"], l["entry_px"], v)
            rec[v] = r
            rec[v + "_how"] = how
        r, _ = sim_variant(bars, l["symbol"], l["entry_date"], l["entry_px"], "A")
        rec["A_hold_t5"] = r
        # V4：只保留"买入时已有底仓"的腿（其余按 0% 计入，代表"不做这笔"）
        rec["V4_precond"] = rec.get("V3") if l.get("had_position") else 0.0
        # V5：高位（≥70% 分位）用 V3，低位/未知持有到 T+5
        rec["V5_bypos"] = rec.get("V3") if (pos is not None and pos >= 0.7) else rec.get("A_hold_t5")
        out["legs"].append(rec)
        for k in variants:
            vals[k].append((l["exit_date"], rec.get(KEYMAP[k])))
    for k in variants:
        sm = M.stat([v for _, v in vals[k]])
        pairs = [(d, v) for d, v in vals[k] if v is not None]
        sm["block_t"] = M.block_t(pairs)
        _d = sorted(d for d, _ in pairs)
        if _d:
            mid = _d[len(_d) // 2]
            sm["H1"] = M.stat([v for d, v in pairs if d <= mid])
            sm["H2"] = M.stat([v for d, v in pairs if d > mid])
        out["variants"][k] = sm
    prod_pairs = [(r["exit_date"], r["prod"]) for r in out["legs"]]
    prod = M.stat([v for _, v in prod_pairs])
    prod["block_t"] = M.block_t(prod_pairs)
    _pd = sorted(d for d, _ in prod_pairs)
    if _pd:
        _mid = _pd[len(_pd) // 2]
        prod["H1"] = M.stat([v for d, v in prod_pairs if d <= _mid])
        prod["H2"] = M.stat([v for d, v in prod_pairs if d > _mid])
    out["variants"]["BASE_prod"] = prod

    # 打印
    print("\n【阶段 1 变体对照】账户=%s  腿数=%d（来源 %s）" % ("/".join(accts), len(strat), meta.get("_pulled_at")))
    print(M.md_table(["变体", "n", "胜率", "均值%", "中位%", "最差%", "日度t", "块状t", "H1均值", "H2均值"], [
        [k, (out["variants"][k] or {}).get("n"), (out["variants"][k] or {}).get("win"),
         (out["variants"][k] or {}).get("mean"), (out["variants"][k] or {}).get("median"),
         (out["variants"][k] or {}).get("min"), (out["variants"][k] or {}).get("t"),
         ((out["variants"][k] or {}).get("block_t") or {}).get("t"),
         ((out["variants"][k] or {}).get("H1") or {}).get("mean"),
         ((out["variants"][k] or {}).get("H2") or {}).get("mean")]
        for k in ("BASE_prod",) + variants]))
    by_how = collections.Counter((r.get("V3_how") or "?") for r in out["legs"])
    print("\n  V3 出场方式分布：", dict(by_how))
    hi = [r for r in out["legs"] if (r.get("pos_pct") is not None and r["pos_pct"] >= 0.7)]
    lo = [r for r in out["legs"] if (r.get("pos_pct") is not None and r["pos_pct"] < 0.7)]
    print("  高/低位分档（入场 60 日分位）：高位 n=%d 生产均值 %s / V3 均值 %s ｜ 低位 n=%d 生产均值 %s / 持T+5 均值 %s"
          % (len(hi), bucket_rows([r["prod"] for r in hi]).get("mean"), bucket_rows([r.get("V3") for r in hi]).get("mean"),
             len(lo), bucket_rows([r["prod"] for r in lo]).get("mean"), bucket_rows([r.get("A_hold_t5") for r in lo]).get("mean")))
    out["buckets"] = {"high": {"n": len(hi), "prod": bucket_rows([r["prod"] for r in hi]),
                               "V3": bucket_rows([r.get("V3") for r in hi])},
                      "low": {"n": len(lo), "prod": bucket_rows([r["prod"] for r in lo]),
                              "A_hold_t5": bucket_rows([r.get("A_hold_t5") for r in lo])}}

    if args.replay:
        rlegs = M.load_replay_legs()
        rb = M.ReplayBars()
        rep = {}
        if rlegs:
            for nm, cfg in (("A_hold_t5", {}), ("V1_tp3", {"tp": 3}), ("V1_tp5", {"tp": 5})):
                v = [M.ruler_rules(rb, t["ts_code"], t["arm_date"], t["entry_px"], 5, **cfg) for t in rlegs]
                p = [(t["arm_date"], x) for t, x in zip(rlegs, v) if x is not None]
                rep[nm] = dict(M.stat([x for _, x in p]), block_t=M.block_t(p))
            out["replay"] = rep
            print("\n【参考：428 触发腿（老样本，日线口径）】")
            print(M.md_table(["变体", "n", "胜率", "均值%", "中位%", "日度t", "块状t"],
                             [[k, v.get("n"), v.get("win"), v.get("mean"), v.get("median"), v.get("t"),
                               (v.get("block_t") or {}).get("t")] for k, v in rep.items()]))

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n→ %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

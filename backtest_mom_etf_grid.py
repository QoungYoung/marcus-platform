# -*- coding: utf-8 -*-
"""t-mom-etf 科技ETF动量趋势 · 网格回测（点位、无前视）。

口径：tech7 池，tushare fund_daily 日线（同生产 _fetch_etf_kline），
20 日动量 = close[t]/close[t-20]-1（用全局交易日历+前向填充对齐）；
贪婪 = arkvol tech-hardware-greed 250 日分位（日期 <= as_of，点位）；
T+1（只卖隔日仓）+ 双边滑点 + 单边费率。
退出维度：定周期轮动(现方案) / 固定持有 5/10/20 日 / 动量转负止损 / 回撤止损。
贪婪维度：GREED_CAP ∈ {0.80,0.85,0.90,0.95}。
周期/持仓维度：REBALANCE_EVERY ∈ {5,10,20} × TOP_N ∈ {2,3,5}。
运行位置：/app/data/backtest_mom_etf_grid.py（在 backend 容器内执行）。
"""
import os, json, time
from datetime import datetime

from app.services.golden_pit_config import TECH_SECTOR_POOL
from app.services.arkvol_service import ArkvolService
from app.core.trading._api_config import get_tushare_pro

SLIP = float(os.getenv("BT_SLIP", "0.001"))   # 单边滑点 0.1%
FEE  = float(os.getenv("BT_FEE", "0.0002"))   # 单边费率 0.02%
SIM_START = os.getenv("BT_START", "2024-01-02")
SIM_END   = os.getenv("BT_END",   "2026-08-21")
GREED_START = "2025-01-02"  # 贪婪数据起点


def _norm(etf):
    s = etf.upper()
    if s.startswith("SH"): return s[2:] + ".SH"
    if s.startswith("SZ"): return s[2:] + ".SZ"
    return s + ".SH"


def load_closes(adj=True):
    """拉取 tech7 ETF 日线，返回 {etf: {date: close}}。adj=True 用 qfq 前复权（拆股/分红对齐）。"""
    pro = get_tushare_pro()
    series = {}
    for pk, e in TECH_SECTOR_POOL.items():
        etf = e["etf_code"]
        tsc = _norm(etf)
        df = pro.fund_daily(ts_code=tsc, start_date="20231001", end_date="20260822")
        if df is None or df.empty:
            raise RuntimeError("no data " + etf)
        df = df.sort_values("trade_date")
        dates = df["trade_date"].tolist()
        raw = [float(x) for x in df["close"]]
        if adj:
            try:
                afd = pro.fund_adj(ts_code=tsc, start_date="20231001", end_date="20260822")
                af_map = dict(zip(afd["trade_date"], [float(x) for x in afd["adj_factor"]])) if afd is not None and not afd.empty else {}
            except Exception:
                af_map = {}
            carry = []; last = None
            for d in dates:
                if d in af_map:
                    last = af_map[d]
                carry.append(last)
            af_latest = carry[-1] if carry and carry[-1] is not None else 1.0
            if af_latest and af_latest > 0:
                qfq = [raw[i] * (carry[i] if carry[i] else 1.0) / af_latest for i in range(len(dates))]
            else:
                qfq = raw
        else:
            qfq = raw
        series[etf] = {str(d[:4] + "-" + d[4:6] + "-" + d[6:]): qfq[i] for i, d in enumerate(dates)}
    return series


def load_greed():
    svc = ArkvolService()
    pl = svc.fetch_tech_greed(days=2000)
    data = pl.get("data") if isinstance(pl, dict) else pl
    g = {}
    for code, arr in (data or {}).items():
        d = {}
        for r in arr or []:
            if (r or {}).get("date") and (r or {}).get("greed") is not None:
                d[str(r["date"])] = float(r["greed"])
        if d:
            g[str(code)] = d
    return g


def pct_of(greed_map, etf6, as_of):
    """点位贪婪分位：仅用日期<=as_of 的观测，最近 250 个里最新值分位。"""
    ser = greed_map.get(etf6, {})
    items = sorted((d, v) for d, v in ser.items() if d <= as_of)
    if not items:
        return None
    latest = items[-1][1]
    recent = [v for _, v in items[-250:]]
    if len(recent) < 20:
        return None
    return sum(1 for v in recent if v <= latest) / len(recent)


def build_matrices(closes):
    cal = sorted(set().union(*[set(s.keys()) for s in closes.values()]))
    idx = {d: i for i, d in enumerate(cal)}
    ff = {}
    for etf, s in closes.items():
        arr = []
        prev = None
        for d in cal:
            prev = s.get(d, prev)
            arr.append(prev if prev is not None else 0.0)
        ff[etf] = arr
    return cal, idx, ff


def momentum(ff, etf, i):
    c0 = ff[etf][i]
    c20 = ff[etf][i - 20] if i >= 20 else None
    if c20 is None or c20 <= 0 or c0 <= 0:
        return None
    return c0 / c20 - 1.0


def run(cal, idx, ff, greed_map, exit_mode, rebal, top_n, greed_cap, sim_start, sim_end, hold_days=0, dd=0.0):
    i0 = idx[sim_start]; i1 = idx[sim_end]
    cash = 1.0
    positions = {}      # sym -> {sh:shares, avg, entry_i}
    value_hist = []
    realized = []       # positive/negative pnl on sells
    traded = 0.0        # notional traded (sum buy+sell)
    last_rebal = -(10 ** 9)

    def pv(i):
        v = cash
        for s, p in positions.items():
            v += p["sh"] * ff[s][i]
        return v

    def sell(s, i, reason):
        nonlocal cash, traded, realized
        p = positions.get(s)
        if not p: return
        px = ff[s][i] * (1 - SLIP)
        notional = p["sh"] * px
        fee = notional * FEE
        cash += notional - fee
        traded += notional
        pnl = (px - p["avg"] * (1 + SLIP)) * p["sh"] - fee
        realized.append(pnl)
        del positions[s]

    def buy(s, amount, i):
        nonlocal cash, traded
        px = ff[s][i] * (1 + SLIP)
        sh = amount / px if px > 0 else 0.0
        if sh <= 0: return
        fee = amount * FEE
        cash -= amount + fee
        traded += amount
        if s in positions:
            old = positions[s]
            tot = old["sh"] + sh
            positions[s] = {"sh": tot, "avg": (old["avg"] * old["sh"] + px * sh) / tot, "entry_i": old["entry_i"]}
        else:
            positions[s] = {"sh": sh, "avg": px, "entry_i": i}

    def select_target(i):
        if i < 20: return []
        scored = []
        for etf in ff:
            m = momentum(ff, etf, i)
            if m is None: continue
            gp = pct_of(greed_map, etf[2:] if etf[:2] in ("SH", "SZ") else etf, cal[i])
            if gp is not None and gp > greed_cap:
                continue
            scored.append((m, etf))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_n]]

    for i in range(i0, i1 + 1):
        date = cal[i]
        # --- daily forced exits (non-periodic) evaluated at close ---
        if exit_mode == "fixedhold" and hold_days:
            for s in list(positions):
                if (i - positions[s]["entry_i"]) >= hold_days:
                    sell(s, i, "fixedhold")
        if exit_mode == "momstop":
            for s in list(positions):
                m = momentum(ff, s, i)
                if m is not None and m <= 0:
                    sell(s, i, "momstop")
        if exit_mode == "ddstop" and dd:
            for s, p in list(positions.items()):
                if ff[s][i] <= p["avg"] * (1 - dd):
                    sell(s, i, "ddstop")
        # --- periodic rebalance ---
        if exit_mode in ("fixedhold",) and False:
            pass
        if (i - last_rebal) >= rebal:
            target = select_target(i)
            V = pv(i)
            # sell not-in-target
            for s in list(positions):
                if s not in target:
                    sell(s, i, "rebalance")
            # equal-weight reconcile target
            if target:
                per = V / len(target)
                for s in target:
                    cur = positions.get(s, {}).get("sh", 0.0) * ff[s][i]
                    diff = per - cur
                    if diff > 0 and cash > 0:
                        buy(s, min(diff, cash), i)
                    elif diff < 0:
                        sell(s, i, "rebalance")
            last_rebal = i
        value_hist.append((date, pv(i)))

    # metrics
    vals = [v for _, v in value_hist]
    n = len(vals)
    if n < 2:
        return None
    end_v = vals[-1]; start_v = vals[0] if vals[0] > 0 else 1.0
    ann = (end_v / start_v) ** (252.0 / max(n - 1, 1)) - 1.0
    peak = -1e18; mdd = 0.0
    for v in vals:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak)
    gros = sum(x for x in realized if x > 0); grosl = -sum(x for x in realized if x < 0)
    pf = (gros / grosl) if grosl > 0 else (float("inf") if gros > 0 else 0.0)
    avg_v = sum(vals) / n
    years = n / 252.0
    turnover = traded / avg_v / years if years > 0 else 0.0
    return {"ann": ann, "mdd": mdd, "turnover": turnover, "pf": pf,
            "end": end_v, "n": n, "trades": len(realized)}


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and x != x): return "  n/a "
    if x == float("inf"): return "  inf "
    return ("%." + str(nd) + "f") % x


def main():
    print("loading data ...")
    closes = load_closes()
    cal, idx, ff = build_matrices(closes)
    print("calendar %s -> %s  (%d bars), %d ETFs" % (cal[0], cal[-1], len(cal), len(closes)))
    greed_map = load_greed()
    print("greed keys %d, first %s" % (len(greed_map), min((min(v.keys()) for v in greed_map.values() if v), default="n/a")))

    out = {}
    def run_cfg(name, **kw):
        return run(cal, idx, ff, greed_map, kw["exit"], kw.get("rebal", 10), kw.get("top", 3), kw.get("cap", 0.90),
                   kw.get("start", SIM_START), SIM_END, kw.get("hold", 0), kw.get("dd", 0.0))

    # A) exit dimension (baseline REBAL=10 TOP=3 CAP=0.9)
    print("\n=== A) 退出维度 (REBAL=10, TOP=3, CAP=0.9) ===")
    exit_cfgs = [
        ("定周期轮动(baseline)", dict(exit="periodic")),
        ("固定持有5日", dict(exit="fixedhold", hold=5)),
        ("固定持有10日", dict(exit="fixedhold", hold=10)),
        ("固定持有20日", dict(exit="fixedhold", hold=20)),
        ("动量转负止损", dict(exit="momstop")),
        ("回撤止损5%", dict(exit="ddstop", dd=0.05)),
        ("回撤止损8%", dict(exit="ddstop", dd=0.08)),
    ]
    for name, kw in exit_cfgs:
        res = run_cfg(name, **kw)
        print("%-14s 年化=%s 回撤=%s 换手=%s PF=%s 终值=%s" % (name, fmt(res["ann"]), fmt(res["mdd"]), fmt(res["turnover"]), fmt(res["pf"], 2), fmt(res["end"], 3)))
        out[name] = res

    # B) greed dimension (baseline periodic REBAL=10 TOP=3) over 2025+ (greed data available)
    print("\n=== B) 贪婪分位 CAP (periodic, REBAL=10, TOP=3, 区间2025-01~end) ===")
    for cap in (0.80, 0.85, 0.90, 0.95):
        res = run(cal, idx, ff, greed_map, "periodic", 10, 3, cap, GREED_START, SIM_END)
        print("CAP=%.2f  年化=%s 回撤=%s 换手=%s PF=%s" % (cap, fmt(res["ann"]), fmt(res["mdd"]), fmt(res["turnover"]), fmt(res["pf"], 2)))

    # C) period x top_n (periodic, CAP=0.9)
    print("\n=== C) REBALANCE_EVERY x TOP_N (periodic, CAP=0.9) ===")
    print("%-3s %-6s %-12s %-12s %-12s %-12s" % ("rebal", "top", "年化", "回撤", "换手", "PF"))
    for rebal in (5, 10, 20):
        for top in (2, 3, 5):
            res = run(cal, idx, ff, greed_map, "periodic", rebal, top, 0.90, SIM_START, SIM_END)
            print("%-3d %-6d %-12s %-12s %-12s %-12s" % (rebal, top, fmt(res["ann"]), fmt(res["mdd"]), fmt(res["turnover"]), fmt(res["pf"], 2)))

    # D) 口径对齐诊断：成本/门控敏感性 (periodic, REBAL=10, TOP=3)
    print("\n=== D) 口径对齐诊断 (periodic, REBAL=10, TOP=3, qfq) ===")
    def run_diag(cap, slip, fee):
        _g = globals()
        _g["SLIP"] = slip; _g["FEE"] = fee
        try:
            return run(cal, idx, ff, greed_map, "periodic", 10, 3, cap, SIM_START, SIM_END)
        finally:
            _g["SLIP"] = 0.001; _g["FEE"] = 0.0002
    r1 = run_diag(0.90, 0.001, 0.0002)
    print("qfq+成本+门控0.90 年化=%s 回撤=%s PF=%s 终值=%s" % (fmt(r1["ann"]), fmt(r1["mdd"]), fmt(r1["pf"],2), fmt(r1["end"],3)))
    r2 = run_diag(1.00, 0.0, 0.0)
    print("qfq+无成本+无门控 年化=%s 回撤=%s PF=%s 终值=%s   <-- 天花板(理想口径)" % (fmt(r2["ann"]), fmt(r2["mdd"]), fmt(r2["pf"],2), fmt(r2["end"],3)))
    r3 = run_diag(0.90, 0.0, 0.0)
    print("qfq+无成本+门控0.90 年化=%s 回撤=%s PF=%s 终值=%s" % (fmt(r3["ann"]), fmt(r3["mdd"]), fmt(r3["pf"],2), fmt(r3["end"],3)))

    print("\nDONE")


if __name__ == "__main__":
    main()
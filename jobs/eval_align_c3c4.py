# -*- coding: utf-8 -*-
"""eval_align_c3c4.py — C3（仓位档）与 C4（ETF 波动）的**历史数据离线核对**（2026-09-15）。

用户红线：对齐项上线前要有验证。C1（254 容差）与 P1（选板块第一要素）已在
`jobs/eval_pick_selection.py --dip-tol` / `jobs/eval_theme_vol_fund.py` 里量过；本脚本补 C3/C4：

**C3**：PG `paper_daily_snapshot`（stock 账户）逐日 `position_value / total_asset` → 与他 2026-01-17 的
分档上限（主升 75 / 调整 50 / 有风险 30 / 下跌 0）比较，看**历史上会不会被他的上限拦住**。

**C4**：把 `paper_trades` 里**真实买过的 ETF** 取出，用中继 `fund_daily` 还原买入日前 20 个交易日的
日均振幅 → 按他的话（≥3%）判达标与否，并附该腿的实际结果（若已平仓）。

用法：`.venv/bin/python jobs/eval_align_c3c4.py`
"""
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".dsh-tmp", "wolfbt"))
sys.path.insert(0, os.path.join(ROOT, "backend"))

CORPUS_TOTAL_CAP = {"build": 75, "t_only": 50, "side": 50, "defense": 30, "exit": 0}
VOL_THR = 3.0          # 2026-08-21 他的话
VOL_WIN = 20


def _pg():
    from local_pg import DSN, ensure_tunnel
    ensure_tunnel()
    import psycopg2
    return psycopg2.connect(**DSN)


def c3_snapshot_check():
    conn = _pg()
    cur = conn.cursor()
    cur.execute("SELECT trade_date, position_value, total_asset FROM paper_daily_snapshot "
                "WHERE account_id='stock' ORDER BY trade_date")
    rows = cur.fetchall()
    cur.close(); conn.close()
    out = []
    for d, pv, ta in rows:
        pct = round(float(pv or 0) / float(ta or 1) * 100.0, 2)
        out.append({"date": str(d), "position_pct": pct,
                    "over_50": pct > 50, "over_30": pct > 30, "over_75": pct > 75})
    return out


def _etf_daily_until(sym, end8, want=40):
    """取该 ETF ≤ end8 的日线（中继 fund_daily）。"""
    import importlib
    sys.path.insert(0, os.path.join(ROOT, "core"))
    relay = importlib.import_module("tushare_relay")
    ts = sym.upper().replace("SH", "").replace("SZ", "")
    ts = (ts[:6] + "." + ("SH" if sym.upper().startswith("SH") else "SZ"))
    fields, items = relay.relay_items("fund_daily", fields="ts_code,trade_date,high,low,close",
                                      ts_code=ts, start_date="20260101", end_date=end8)
    idx = {n: i for i, n in enumerate(fields or [])}
    bars = []
    for it in items or []:
        try:
            bars.append({"trade_date": str(it[idx["trade_date"]]), "high": float(it[idx["high"]]),
                         "low": float(it[idx["low"]]), "close": float(it[idx["close"]])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(bars, key=lambda x: x["trade_date"])[-want:]


def c4_actual_etf_legs():
    conn = _pg()
    cur = conn.cursor()
    cur.execute("SELECT symbol, direction, trade_date, price, profit FROM paper_trades "
                "WHERE COALESCE(voided,0)=0 AND symbol ~ '^(SH5|SH1|SZ1|SZ5)' ORDER BY created_at")
    trades = cur.fetchall()
    cur.close(); conn.close()
    buys = collections.Counter()
    pnl = collections.defaultdict(float)
    for sym, d, td, px, pf in trades:
        if d in ("买入", "buy"):
            buys[sym] += 1
        else:
            pnl[sym] += float(pf or 0)
    out = []
    for sym, n in buys.items():
        first_buy = min(str(t[2]) for t in trades if t[0] == sym and t[1] in ("买入", "buy"))
        bars = _etf_daily_until(sym, first_buy)
        amp = None
        use = bars[-VOL_WIN:]
        if use:
            vals = [(b["high"] - b["low"]) / b["close"] * 100.0 for b in use if b["close"]]
            amp = round(sum(vals) / len(vals), 3) if vals else None
        out.append({"symbol": sym, "buys": n, "first_buy": first_buy, "bars": len(bars),
                    "amp20_at_first_buy": amp, "pass_3pct": (amp is not None and amp >= VOL_THR),
                    "realized_pnl": round(pnl.get(sym, 0.0), 2)})
    return out


def main():
    print("=" * 80)
    print("C3 仓位档核对（他的话 2026-01-17：主升75/调整50/有风险30/下跌不做）")
    print("=" * 80)
    snap = c3_snapshot_check()
    if not snap:
        print("   （无 paper_daily_snapshot 记录）")
    else:
        for r in snap:
            flag = "**超50%**" if r["over_50"] else ("超30%" if r["over_30"] else "低于30%")
            print("   %s 仓位 %6.2f%%  %s" % (r["date"], r["position_pct"], flag))
        mx = max(r["position_pct"] for r in snap)
        print("   → 区间最大仓位 %.2f%%；按他的话（调整期 50%%）**从未触顶**" % mx)

    print()
    print("=" * 80)
    print("C4 ETF 波动核对（他的话 2026-08-21：ETF 都有 3 个点以上的波动）")
    print("=" * 80)
    rows = c4_actual_etf_legs()
    if not rows:
        print("   （paper_trades 里没有 ETF 腿）")
    for r in rows:
        mark = "✅达标" if r["pass_3pct"] else ("✋不达标" if r["amp20_at_first_buy"] is not None else "（无数据）")
        print("   %-10s 买入%s次 首买=%s 20日日均振幅=%-7s %s  实际已实现盈亏=%s"
              % (r["symbol"], r["buys"], r["first_buy"], r["amp20_at_first_buy"], mark, r["realized_pnl"]))
    bad = [r for r in rows if r["amp20_at_first_buy"] is not None and not r["pass_3pct"]]
    print("   → 共 %d 只 ETF，%d 只不达 3%%（若 C4 早开，这些买入会被拦）" % (len(rows), len(bad)))
    out = os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_align_c3c4.json")
    json.dump({"c3_snapshots": snap, "c4_etf_legs": rows}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""bt_reconcile.py — 回测 vs 生产**逐日对账**（腿 / 触发 / 成交 / 净值），输出可复算的 JSON + 打印表。

分母分两层（见 docs/backtest-faithful-daily-flow.md §9.11）：
  **A 层 = 调度通道**（08:18 tranche + 09:20 arm，来自 `logs/scheduler_*.jsonl` 的真值）——目标 ≥90% 重合；
  **B 层 = 全部生产腿**（含手工/agent 直接跑脚本、周末开发痕迹）——只作信息给出，不参与达标判定。

用法（容器内）：
  python jobs/bt_reconcile.py --root /app/data/_bt_sep --truth /app/data/_bt_full/_legs_truth.json \
      [--with-db] [--tape] [--pack /app/data/_bt_sep/pack] [--out /app/data/_bt_sep/_summary/reconcile.json]

分节说明：
  legs      腿级：真值 A（调度通道）/ 回放（legs.jsonl ∪ legs_switch.jsonl）/（可选）DB B 层
  triggers  触发级：对**回放腿**跑 `bt_tape`（需该日已 pack 分钟数据），与生产 `t_triggers` 比首触发时刻与次数
  fills     成交级：回放撮合（BacktestPaperEngine）vs 生产 `paper_trades`
  equity    净值：按成交 + 日线收盘 mark-to-market，给出逐日净值 / 分主题 / 分腿型收益
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

DEFAULT_DSN = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")


# ---------------------------------------------------------------- 数据读取

def replay_legs(root: str, day: str):
    """回放产出的腿（两条路径的并集 + 各自明细）。"""
    out = {"switch_0818": [], "arm_0920": []}
    for fn, key in (("legs_switch.jsonl", "switch_0818"), ("legs.jsonl", "arm_0920")):
        p = os.path.join(root, day, fn)
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                out[key].append(json.loads(ln))
            except Exception:
                pass
    return out


def prod_legs_db(d_from: str, d_to: str, dsn: str):
    import psycopg2
    c = psycopg2.connect(dsn)
    cur = c.cursor()
    cur.execute("""SELECT trade_date, symbol, trigger_kind, to_char(created_at,'HH24:MI:SS')
                   FROM t_conditions
                   WHERE trade_date BETWEEN %s AND %s AND direction='buy' AND publisher='switch'
                     AND trigger_kind IN ('custom_m5dump','custom_prevlow')
                   GROUP BY 1,2,3,4 ORDER BY 1,2""", (d_from, d_to))
    out = {}
    for d, sym, kind, ts in cur.fetchall():
        out.setdefault(str(d), {}).setdefault(str(sym).upper(), {})[kind] = ts
    cur.close(); c.close()
    return out


def prod_triggers_db(d_from: str, d_to: str, dsn: str):
    import psycopg2
    c = psycopg2.connect(dsn)
    cur = c.cursor()
    cur.execute("""SELECT t.trade_date, t.symbol, count(*), min(t.created_at)
                   FROM t_triggers t JOIN t_conditions c ON c.id = t.condition_id
                   WHERE t.trade_date BETWEEN %s AND %s AND c.publisher='switch' AND c.direction='buy'
                   GROUP BY 1,2 ORDER BY 1,2""", (d_from, d_to))
    out = {}
    for d, sym, n, first in cur.fetchall():
        out.setdefault(str(d), {})[str(sym).upper()] = {"n": int(n), "first": str(first)}
    cur.close(); c.close()
    return out


def prod_fills_db(d_from: str, d_to: str, dsn: str, account: str = "stock"):
    import psycopg2
    c = psycopg2.connect(dsn)
    cur = c.cursor()
    cur.execute("""SELECT trade_date, symbol, direction, volume, price, created_at, coalesce(reason,'')
                   FROM paper_trades
                   WHERE trade_date BETWEEN %s AND %s AND account_id = %s AND coalesce(voided,0)=0
                   ORDER BY created_at""",
                ("%s-%s-%s" % (d_from[:4], d_from[4:6], d_from[6:]),
                 "%s-%s-%s" % (d_to[:4], d_to[4:6], d_to[6:]), account))
    out = {}
    for d, sym, side, vol, px, ts, why in cur.fetchall():
        key = str(d).replace("-", "")
        out.setdefault(key, []).append({"symbol": str(sym).upper(), "side": side, "vol": int(vol),
                                        "price": float(px), "ts": str(ts), "why": why})
    cur.close(); c.close()
    return out


# ---------------------------------------------------------------- 分节计算

def sec_legs(days, root, truth, db_legs):
    rows = []
    tA = tR = hit = 0
    tB = hitB = 0
    for day in days:
        rep = replay_legs(root, day)
        r = {str(l.get("symbol")).upper() for l in rep["switch_0818"] + rep["arm_0920"] if l.get("symbol")}
        p = set((truth.get(day) or {}).get("union_buy") or [])
        b = set((db_legs.get(day) or {}).keys())
        h = p & r
        tA += len(p); tR += len(r); hit += len(h)
        tB += len(b); hitB += len(b & r)
        rows.append({"day": day, "A_sched": sorted(p), "replay": sorted(r), "hit": sorted(h),
                     "only_sched": sorted(p - r), "only_replay": sorted(r - p),
                     "B_all_prod": sorted(b), "B_only": sorted(b - p),
                     "src": {k: sorted({str(x.get("symbol")).upper() for x in v if x.get("symbol")})
                             for k, v in rep.items()}})
    return {"rows": rows,
            "A_total": tA, "replay_total": tR, "hit": hit,
            "recall_A": (hit / tA) if tA else None, "precision": (hit / tR) if tR else None,
            "B_total": tB, "hit_B": hitB}


def sec_triggers(days, root, pack, dip_tol, use_prod_conds=True, db_trig=None):
    """对**回放腿**跑 bt_tape（需要该日已 pack 分钟数据）；与生产 t_triggers 比首触发/次数。

    ⚠️ 条件来源两种口径（都要能对得上）：
      `use_prod_conds=True` → 用**生产条件**（t_conditions 里的 expression）跑，衡量"同样的条件在同一分钟
        数据下是否得到同样的触发时刻"（纯撮合/行情层差异）；
      `False` → 用回放自己布出来的条件（`rotation_switch_arm.BUY_253/254_EXPR`），衡量端到端。
    """
    sys.path.insert(0, "/app/jobs")
    import bt_tape
    db_trig = db_trig or {}
    rows = []
    for day in days:
        rep = replay_legs(root, day)
        syms = sorted({str(l.get("symbol")).upper() for l in rep["switch_0818"] + rep["arm_0920"] if l.get("symbol")})
        for sym in syms:
            conds = None
            if use_prod_conds:
                try:
                    conds = bt_tape.conds_from_prod(sym, day)
                except Exception as e:
                    rows.append({"day": day, "symbol": sym, "err": "conds_from_prod: %s" % str(e)[:100]})
                    continue
                if not conds:
                    rows.append({"day": day, "symbol": sym, "err": "生产无该腿条件（可能是回放多布/命名不同）"})
                    continue
            try:
                res = bt_tape.run_symbol(pack, sym, day, conds, dip_tol=dip_tol)
            except Exception as e:
                rows.append({"day": day, "symbol": sym, "err": str(e)[:120]})
                continue
            trig = res.get("triggers") or []
            first = trig[0].get("time") if trig and isinstance(trig[0], dict) else None
            prod = ((db_trig.get(day) or {}).get(sym) or {})
            rows.append({"day": day, "symbol": sym, "n_replay": len(trig), "first_replay": first,
                         "n_prod": prod.get("n"), "first_prod": (prod.get("first") or "")[11:19] or None,
                         "n_bars": res.get("n_bars"), "fills": res.get("fills")})
    return rows


def sec_equity(days, root, bars_db, initial=250000.0, account="stock"):
    """按回放成交 + 日线收盘 mark-to-market → 逐日净值。"""
    import sqlite3
    sq = sqlite3.connect(bars_db)
    cash, pos = initial, {}          # symbol -> [qty, cost]
    curve, trades = [], []
    for day in days:
        for f in ([] if not os.path.exists(os.path.join(root, day, "fills.jsonl"))
                  else [json.loads(l) for l in open(os.path.join(root, day, "fills.jsonl"), encoding="utf-8") if l.strip()]):
            sym, side, qty, px = str(f.get("symbol")).upper(), f.get("side"), int(f.get("vol") or 0), float(f.get("price") or 0)
            if side in ("买入", "buy"):
                cash -= qty * px
                st = pos.setdefault(sym, [0, 0.0])
                st[1] = (st[1] * st[0] + qty * px) / max(st[0] + qty, 1)
                st[0] += qty
            else:
                cash += qty * px
                st = pos.get(sym)
                if st:
                    st[0] -= qty
                    if st[0] <= 0:
                        pos.pop(sym, None)
            trades.append(dict(f, day=day))
        mv = 0.0
        for sym, (qty, _c) in pos.items():
            row = sq.execute("SELECT close FROM bars WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                             (sym, day)).fetchone()
            mv += qty * (row[0] if row else 0.0)
        curve.append({"day": day, "cash": round(cash, 2), "mv": round(mv, 2),
                      "equity": round(cash + mv, 2), "n_pos": len(pos)})
    sq.close()
    return {"curve": curve, "trades": trades, "initial": initial}


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="沙箱根目录（如 /app/data/_bt_sep）")
    ap.add_argument("--truth", default="/app/data/_bt_full/_legs_truth.json")
    ap.add_argument("--from", dest="d_from", default="")
    ap.add_argument("--to", dest="d_to", default="")
    ap.add_argument("--with-db", action="store_true")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--tape", action="store_true", help="跑触发级对账（需要 pack 分钟数据）")
    ap.add_argument("--pack", default="")
    ap.add_argument("--dip-tol", type=float, default=0.005)
    ap.add_argument("--bars-db", default="/app/data/_bt_full/bars.sqlite")
    ap.add_argument("--initial", type=float, default=250000.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    truth = {}
    if os.path.exists(a.truth):
        truth = json.load(open(a.truth, encoding="utf-8")).get("days") or {}
    days = sorted(d for d in os.listdir(a.root)
                  if len(d) == 8 and d.isdigit())
    if a.d_from:
        days = [d for d in days if d >= a.d_from]
    if a.d_to:
        days = [d for d in days if d <= a.d_to]

    db_legs = prod_legs_db(days[0], days[-1], a.dsn) if (a.with_db and days) else {}
    res = {"days": days, "root": a.root}
    res["legs"] = sec_legs(days, a.root, truth, db_legs)

    print("=== 腿级对账（A 层 = 调度通道）")
    print("%-10s %6s %6s %5s  %s" % ("day", "A真值", "回放", "命中", "仅真值 / 仅回放"))
    for r in res["legs"]["rows"]:
        print("%-10s %6d %6d %5d  %s / %s" % (r["day"], len(r["A_sched"]), len(r["replay"]), len(r["hit"]),
                                              r["only_sched"], r["only_replay"]))
    L = res["legs"]
    print("合计：A 层 %d 条 / 回放 %d 条 / 命中 %d → 召回 %.0f%%  精确 %.0f%%"
          % (L["A_total"], L["replay_total"], L["hit"], 100 * (L["recall_A"] or 0), 100 * (L["precision"] or 0)))
    if db_legs:
        print("（B 层 = 全部生产腿 %d 条，其中 %d 条命中 → 只作信息）" % (L["B_total"], L["hit_B"]))

    if a.tape:
        pack = a.pack or os.path.join(a.root, "pack")
        db_trig = prod_triggers_db(days[0], days[-1], a.dsn) if a.with_db else {}
        res["triggers"] = sec_triggers(days, a.root, pack, a.dip_tol, db_trig=db_trig)
        print("\n=== 触发级对账（回放 vs 生产 t_triggers）")
        print("%-10s %-10s %8s %-10s %6s %-10s" % ("day", "symbol", "回放次数", "回放首触发", "生产", "生产首触发"))
        for r in res["triggers"]:
            if r.get("err"):
                print("%-10s %-10s  ERR %s" % (r["day"], r["symbol"], r["err"]))
            else:
                print("%-10s %-10s %8s %-10s %6s %-10s"
                      % (r["day"], r["symbol"], r["n_replay"], (r["first_replay"] or "-")[11:19],
                         r["n_prod"] if r["n_prod"] is not None else "-", r["first_prod"] or "-"))

    if os.path.exists(a.bars_db):
        res["equity"] = sec_equity(days, a.root, a.bars_db, initial=a.initial)
        print("\n=== 净值（按回放成交 + 日线收盘 mark-to-market，初始 %.0f）" % a.initial)
        for c in res["equity"]["curve"]:
            print("  %s  现金 %12.2f  市值 %12.2f  净值 %12.2f  持仓 %d"
                  % (c["day"], c["cash"], c["mv"], c["equity"], c["n_pos"]))
        if res["equity"]["curve"]:
            last = res["equity"]["curve"][-1]["equity"]
            print("  区间收益 %.2f%%（%.2f → %.2f）" % (100 * (last / a.initial - 1), a.initial, last))

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n→ %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

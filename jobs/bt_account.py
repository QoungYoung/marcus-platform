# -*- coding: utf-8 -*-
"""bt_account.py — **账户级**盘中回放：分钟 bar 触发 → 护栏 → 撮合 → 逐日净值（买入层回测的收口件）。

与前面几件的关系：
  `bt_days.py`    → 逐日**腿**（08:18 switch_builder + 09:20 rotation_switch_arm，时代检查 + 沙箱隔离）
  `bt_tape.py`    → 单标的逐 bar 触发（生产 `t_expr` 求值器；vol_ratio / quote.average / dip_prev_low / vwap_break / index.m5_dump 口径对齐）
  `bt_account.py` → **把当日所有腿的触发按时间排序，喂给一个账户**（现金/持仓/T+1/整手/费用），出逐日净值与分主题/分腿型收益

⚠️ **为什么不用 `backtest_paper.BacktestPaperEngine`**（重要，别改回去）：
   它内部是 `apps/paper-trading/paper_engine.PaperTradingEngine`，而后者是 **PostgreSQL 落地**的
   （`paper_account_info/paper_positions/paper_trades/paper_orders`，默认 `account_id='stock'`）——
   构造它 = 读写**生产模拟盘账户**，`place_order` 会往 `paper_trades` 插生产行（实测第 5 轮那次没插进去是
   因为它在余额/价格校验处失败被 try/except 吞了，属侥幸）。**回测绝不能碰生产 paper_* 表** →
   本驱动自带内存账户，只复用它的**费用率**（`resolve_commission()`，0.1292%/往返）与**语义**
   （T+1 按买入批次、100 股整手、FIFO 成本、跌停禁买）。

口径（逐条对齐生产）：
  · 费用：买 0.000396 / 卖 0.000896（= 0.1292%/往返）；`BT_FEE_PROFILE=legacy` → 0.0005/0.0015。
  · 单笔规模：生产"试仓档"实测 ≈ 25,000 元/笔（09-14 SZ002409 200 股 @125.175 = 25,035；09-15 SZ002156 500 股 @58.042 = 29,021）
    → `--order-budget 25000`，整手向下取整，现金不足缩量，缩到 0 跳过。
  · 护栏：单标的单日**买腿成交 ≤ 2 笔**（`t_gateway.MAX_DAILY_BUY_LEGS=2`，硬拦）；跌停禁买
    （`t_gateway._near_limit_down`：现价 ≤ 昨收×(1−9.8%)）。**日回转 3× 上限已由生产删除（S5, 2026-09-10）→ 不拦**；
    `MAX_SINGLE_ORDER_PCT=5%` 在生产只是告警 → 不拦，只统计超限笔数。
  · T+1：当日买入当日不可卖；卖出量 ≤ 可卖量（非当日批次）。
  · 模式：`hold` = 只买不卖（"买而不卖"基线）；`leg` = 买腿 + 卖腿（`quote.vwap_break` = 黄线跌破直接走）。

用法（容器内）：
  python jobs/bt_account.py --root /app/data/_bt_sep --pack /app/data/_bt_sep/pack \
      --start 20260901 --end 20260914 --initial 250000 --mode hold \
      --out /app/data/_bt_sep/_summary/account_hold.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path[:0] = ["/app", "/app/backend", "/app/jobs"]

SELL_VWAP_EXPR = {"op": "==", "field": "quote.vwap_break", "value": True}
FEE_BUY_FALLBACK = 0.000396      # 万0.86 + 过户费 0.001% + 滑点 0.0003（`eval_aligned_package.py:54` 口径）
FEE_SELL_FALLBACK = 0.000896     # 再加印花税 0.05%
FEE_BUY_LEGACY, FEE_SELL_LEGACY = 0.0005, 0.0015


def fee_rates():
    """费用率：优先用生产 `backtest_paper.resolve_commission()`（同一份口径），失败回退常量。"""
    prof = str(os.getenv("BT_FEE_PROFILE", "") or "").strip().lower()
    if prof == "legacy":
        return FEE_BUY_LEGACY, FEE_SELL_LEGACY, "legacy"
    try:
        _p = "/app/apps/paper-trading"          # 容器布局；`backtest_paper` 里写死的是仓库布局（差一级）
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.append(_p)
        from app.core.trading.backtest_paper import resolve_commission
        b, s, name = resolve_commission()
        return float(b), float(s), str(name)
    except Exception:
        return FEE_BUY_FALLBACK, FEE_SELL_FALLBACK, "fallback_const"


class Account:
    """内存账户：现金 / 持仓（FIFO 批次）/ T+1 / 100 股整手 / 费用。

    与生产 `backtest_paper.BacktestPaperEngine` 的**语义**一致，但**不碰任何生产表**。
    """

    def __init__(self, initial: float, fee_buy: float, fee_sell: float):
        self.initial = float(initial)
        self.cash = float(initial)
        self.fee_buy, self.fee_sell = float(fee_buy), float(fee_sell)
        self.commission = 0.0
        self.pos = {}          # symbol -> {"vol": int, "cost": float, "lots": [(day, vol), ...]}
        self.realized = 0.0

    def available(self, sym: str, day: str) -> int:
        p = self.pos.get(sym)
        if not p:
            return 0
        return sum(v for d, v in p["lots"] if d != day)

    def buy(self, sym: str, px: float, vol: int, day: str):
        if vol <= 0 or vol % 100 != 0 or px <= 0:
            return None
        amount = px * vol
        fee = amount * self.fee_buy
        if amount + fee > self.cash + 1e-6:
            return None
        p = self.pos.setdefault(sym, {"vol": 0, "cost": 0.0, "lots": []})
        p["cost"] = (p["cost"] * p["vol"] + amount) / (p["vol"] + vol)
        p["vol"] += vol
        p["lots"].append((day, vol))
        self.cash -= amount + fee
        self.commission += fee
        return {"fill_price": px, "amount": amount, "fee": fee}

    def sell(self, sym: str, px: float, vol: int, day: str):
        avail = self.available(sym, day)
        vol = min(vol, avail)
        vol = int(vol / 100) * 100
        if vol <= 0 or px <= 0:
            return None
        p = self.pos[sym]
        amount = px * vol
        fee = amount * self.fee_sell
        self.realized += (px - p["cost"]) * vol - fee
        # FIFO 扣批次（当日批次不动）
        left = vol
        for i, (d, v) in enumerate(list(p["lots"])):
            if d == day or left <= 0:
                continue
            take = min(v, left)
            left -= take
            p["lots"][i] = (d, v - take)
        p["lots"] = [(d, v) for d, v in p["lots"] if v > 0]
        p["vol"] -= vol
        self.cash += amount - fee
        self.commission += fee
        if p["vol"] <= 0:
            self.pos.pop(sym, None)
        return {"fill_price": px, "amount": amount, "fee": fee}

    def nav(self, day: str, closes: dict) -> float:
        mv = 0.0
        for sym, p in self.pos.items():
            mv += p["vol"] * float(closes.get(sym) or p["cost"])
        return self.cash + mv


def load_legs(root: str, day: str):
    """当日回放腿（两条路径）→ (买腿, 卖腿)。"""
    buys, sells = [], []
    for fn, src in (("legs_switch.jsonl", "switch_0818"), ("legs.jsonl", "arm_0920")):
        p = os.path.join(root, day, fn)
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                j = json.loads(ln)
            except Exception:
                continue
            sym = str(j.get("symbol") or "").upper()
            if not sym:
                continue
            typ = str(j.get("type") or "")
            rec = {"symbol": sym, "theme": j.get("theme") or j.get("chain") or "",
                   "type": typ or "buy_253/254", "src": src}
            (sells if typ.startswith("sell") else buys).append(rec)
    return buys, sells


def _ts_code(sym: str) -> str:
    """`SH600039` → `600039.SH`（本地日线库 `bars.ts_code` 的格式）。"""
    s = str(sym).strip().upper()
    if len(s) == 8 and s[:2] in ("SH", "SZ", "BJ") and s[2:].isdigit():
        return "%s.%s" % (s[2:], s[:2])
    return s


def near_limit_down(price: float, pre_close: float, is_st: bool = False) -> bool:
    """跌停禁买（`t_gateway._near_limit_down` 口径：主板 10% 留 0.2% 余量、ST 5%）。"""
    if pre_close <= 0:
        return False
    return price <= pre_close * (1 - (0.05 if is_st else 0.098))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--pack", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--initial", type=float, default=250000.0)
    ap.add_argument("--order-budget", type=float, default=25000.0, help="单笔预算（元，整手向下取整）")
    ap.add_argument("--mode", default="hold", choices=["hold", "leg"])
    ap.add_argument("--max-buy-per-symbol-day", type=int, default=2, help="t_gateway.MAX_DAILY_BUY_LEGS")
    ap.add_argument("--dip-tol", type=float, default=0.005)
    ap.add_argument("--bars-db", default="/app/data/_bt_full/bars.sqlite")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    pack = a.pack or os.path.join(a.root, "pack")

    import sqlite3
    sys.path.insert(0, "/app/jobs")
    import bt_tape

    fee_b, fee_s, prof = fee_rates()
    acct = Account(a.initial, fee_b, fee_s)
    sq = sqlite3.connect(a.bars_db)
    days = sorted(d for d in os.listdir(a.root) if len(d) == 8 and d.isdigit())
    if a.start:
        days = [d for d in days if d >= a.start]
    if a.end:
        days = [d for d in days if d <= a.end]

    curve, trades, errs = [], [], []
    theme_agg, type_agg, over5 = {}, {}, 0

    for day in days:
        buys, sells = load_legs(a.root, day)
        events, n_buy_sym = [], {}
        for leg in buys:
            sym = leg["symbol"]
            conds = []
            try:
                conds = bt_tape.conds_from_prod(sym, day)
            except Exception as e:
                errs.append({"day": day, "symbol": sym, "conds_err": str(e)[:100]})
            if not conds:
                import importlib
                arm = importlib.import_module("rotation_switch_arm")
                conds = [{"trigger_kind": "custom_m5dump", "expression": arm.BUY_253_EXPR},
                         {"trigger_kind": "custom_prevlow", "expression": arm.BUY_254_EXPR}]
            try:
                res = bt_tape.run_symbol(pack, sym, day, conds, dip_tol=a.dip_tol, bars_db=a.bars_db)
            except Exception as e:
                errs.append({"day": day, "symbol": sym, "err": str(e)[:140]})
                continue
            for t in res.get("triggers") or []:
                events.append({"time": t["time"], "symbol": sym, "kind": t.get("kind"),
                               "price": float(t["price"]), "theme": leg["theme"], "type": leg["type"],
                               "side": "buy"})
        if a.mode == "leg":
            for leg in sells:
                sym = leg["symbol"]
                if sym not in acct.pos:
                    continue
                try:
                    res = bt_tape.run_symbol(pack, sym, day, [{"trigger_kind": "sell_vwap_break",
                                                               "expression": SELL_VWAP_EXPR}], dip_tol=a.dip_tol,
                                             bars_db=a.bars_db)
                except Exception as e:
                    errs.append({"day": day, "symbol": sym, "err": str(e)[:140]})
                    continue
                for t in res.get("triggers") or []:
                    events.append({"time": t["time"], "symbol": sym, "kind": "sell_vwap_break",
                                   "price": float(t["price"]), "theme": leg["theme"], "type": leg["type"],
                                   "side": "sell"})
        # 按时间顺序执行（买在前，同一 bar 只成交一次/标的/方向）
        events.sort(key=lambda e: (e["time"], e["side"] != "buy"))
        seen = set()
        for ev in events:
            key = (ev["time"], ev["symbol"], ev["side"])
            if key in seen:
                continue
            seen.add(key)
            sym, px = ev["symbol"], ev["price"]
            if ev["side"] == "buy":
                if n_buy_sym.get(sym, 0) >= a.max_buy_per_symbol_day:
                    errs.append({"day": day, "symbol": sym, "skip": "daily_buy_legs_limit"})
                    continue
                row = sq.execute("SELECT pre_close FROM bars WHERE ts_code=? AND trade_date=?",
                                 (_ts_code(sym), day)).fetchone()
                pre = float(row[0]) if row and row[0] else 0.0
                if near_limit_down(px, pre):
                    errs.append({"day": day, "symbol": sym, "skip": "near_limit_down", "price": px, "pre": pre})
                    continue
                nav = acct.nav(day, {})
                budget = min(a.order_budget, acct.cash * 0.98)
                vol = int(budget / max(px, 0.01) / 100) * 100
                if vol <= 0:
                    continue
                if px * vol > nav * 0.05:
                    over5 += 1
                r = acct.buy(sym, px, vol, day)
                if r:
                    n_buy_sym[sym] = n_buy_sym.get(sym, 0) + 1
                    trades.append({"day": day, "time": ev["time"], "symbol": sym, "side": "buy",
                                   "price": px, "vol": vol, "fee": round(r["fee"], 2), "kind": ev["kind"],
                                   "theme": ev["theme"], "type": ev["type"], "src": None})
                else:
                    errs.append({"day": day, "symbol": sym, "reject": "cash_or_lot", "vol": vol})
            else:
                avail = acct.available(sym, day)
                if avail <= 0:
                    errs.append({"day": day, "symbol": sym, "skip": "t1_or_no_position"})
                    continue
                r = acct.sell(sym, px, avail, day)
                if r:
                    trades.append({"day": day, "time": ev["time"], "symbol": sym, "side": "sell",
                                   "price": px, "vol": min(avail, int(avail / 100) * 100),
                                   "fee": round(r["fee"], 2), "kind": ev["kind"],
                                   "theme": ev["theme"], "type": ev["type"]})
        closes = {}
        for sym in list(acct.pos):
            r = sq.execute("SELECT close FROM bars WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                           (_ts_code(sym), day)).fetchone()
            if r and r[0]:
                closes[sym] = float(r[0])
        mv = sum(acct.pos[s]["vol"] * closes.get(s, acct.pos[s]["cost"]) for s in acct.pos)
        curve.append({"day": day, "cash": round(acct.cash, 2), "mv": round(mv, 2),
                      "equity": round(acct.cash + mv, 2), "n_pos": len(acct.pos),
                      "realized_cum": round(acct.realized, 2), "commission_cum": round(acct.commission, 2)})
        print("[acct] %s 当日成交%d 持仓%d 净值%.2f 现金%.2f"
              % (day, len([t for t in trades if t["day"] == day]), len(acct.pos),
                 curve[-1]["equity"], acct.cash), flush=True)

    for t in trades:
        for agg, key in ((theme_agg, t["theme"] or "(无主题)"), (type_agg, t["type"] or "(未知腿型)")):
            d = agg.setdefault(key, {"buy_amount": 0.0, "sell_amount": 0.0, "n_buy": 0, "n_sell": 0,
                                     "fee": 0.0})
            if t["side"] == "buy":
                d["buy_amount"] += t["price"] * t["vol"]
                d["n_buy"] += 1
            else:
                d["sell_amount"] += t["price"] * t["vol"]
                d["n_sell"] += 1
            d["fee"] += t["fee"]
    last = curve[-1]["equity"] if curve else a.initial
    res = {"run": {"root": a.root, "mode": a.mode, "initial": a.initial, "order_budget": a.order_budget,
                   "days": days, "fee_profile": prof, "fee_buy": fee_b, "fee_sell": fee_s,
                   "roundtrip_pct": round((fee_b + fee_s) * 100, 4)},
           "curve": curve, "trades": trades, "errors": errs,
           "by_theme": theme_agg, "by_leg_type": type_agg,
           "summary": {"final_equity": last, "return_pct": round((last / a.initial - 1) * 100, 2),
                       "n_trades": len(trades), "n_buy": len([t for t in trades if t["side"] == "buy"]),
                       "n_sell": len([t for t in trades if t["side"] == "sell"]),
                       "commission_total": round(acct.commission, 2), "realized_total": round(acct.realized, 2),
                       "single_order_over_5pct": over5, "positions_open": len(acct.pos)}}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s = res["summary"]
    print("\n=== 模式 %s  %s → %s   费用口径 %s（往返 %.4f%%）"
          % (a.mode, days[0] if days else "-", days[-1] if days else "-", prof, res["run"]["roundtrip_pct"]))
    print("买 %d 笔 / 卖 %d 笔 / 费用 %.2f / 已实现 %.2f / 期末净值 %.2f（%.2f%%）/ 未平仓 %d"
          % (s["n_buy"], s["n_sell"], s["commission_total"], s["realized_total"], s["final_equity"],
             s["return_pct"], s["positions_open"]))
    if a.out:
        print("→", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""bt_tape.py — **自写 tick 循环**：按 5min bar 逐根重放 253/254 腿的触发与撮合（回测 Pass 2 核心）。

为什么不用现成的 `TBacktestEngine`：它的主循环是为**做T账户**写死的（`trigger_kind` 只认
high_sell/high_sell_buy_back/low_buy/panic_vibrate，买腿还有"无底仓不评估"预拦截）→ 换成我们的
`custom_m5dump` / `custom_prevlow` 时"条件被求值但不产出事件"（实测 completed/events=0，见文档 §9.9）。

本模块做法：
  ① **复用**生产 `t_expr.evaluate_expression()`（同一求值器，支持 `a.b.c` 点路径）；
  ② **自己建快照**：每根 bar 只用 ≤ 该 bar 的数据（无前视），字段与生产 `t_monitor` 对齐：
     `quote.current/open/high/low/pre_close/change_pct/amplitude/vol/amount/average`
     `vol_ratio`（近 N 日**同刻**均量，用 `t_backtest.compute_vol_ratio_base_up_to` 防前视）
     `quote.dip_prev_low`（当日最低 ≤ 前一交易日最低 ×(1+tol)，同 `t_monitor._stock_dip_prev_low`）
     `index.m5_dump`（指数 5min 单根跌幅，同 `t_monitor._index_m5_dump`）
  ③ 触发后交给 `BacktestPaperEngine` 撮合（T+1 / 100 股整手 / 涨跌停 / 费用口径）。

用法（容器内）：
  python jobs/bt_tape.py --date 20260911 --symbol SH600039 --conditions-from-prod \
      --pack /app/data/_bt_full/pack [--dip-tol 0.005] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path[:0] = ["/app", "/app/backend"]


# ── 数据装载 ──────────────────────────────────────────────
def load_m5(pack: str, symbol: str):
    p = os.path.join(pack, "m5", "%s.json" % symbol)
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    for k in d:
        d[k].sort(key=lambda b: b["time"])
    return d


def load_index_m5(pack: str, key: str = "sh"):
    p = os.path.join(pack, "index_m5", "%s.json" % key)
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    bars = [b for day in sorted(d) for b in d[day]]
    bars.sort(key=lambda b: b["time"])
    return bars


def load_daily(pack: str, symbol: str, bars_db: str = "/app/data/_bt_full/bars.sqlite"):
    """日线（含**换手率**，生产 vol_ratio 要用）。pack 里没有 turnover_rate → 从 bars.sqlite 补。"""
    out = []
    p = os.path.join(pack, "stock_daily", "%s.json" % symbol)
    if os.path.exists(p):
        out = json.load(open(p, encoding="utf-8"))
    try:
        import sqlite3
        ts = (symbol[2:8] + "." + symbol[:2]) if symbol[:2] in ("SH", "SZ") else symbol
        c = sqlite3.connect(bars_db)
        tr = {r[0]: r[1] for r in c.execute(
            "SELECT trade_date, turnover_rate FROM bars WHERE ts_code=? AND turnover_rate IS NOT NULL", (ts,))}
        vol = {r[0]: r[1] for r in c.execute(
            "SELECT trade_date, vol FROM bars WHERE ts_code=?", (ts,))}
        c.close()
        for r in out:
            d = str(r.get("trade_date"))
            r["turnover_rate"] = tr.get(d)
            r["day_vol"] = vol.get(d)
    except Exception:
        pass
    return out


# ── 快照 ──────────────────────────────────────────────────
def same_minute_base(all_bars, trade_day: str, lookback: int = 5):
    """近 N 日**同刻**均量（只用 < trade_day 的 bar，防前视）。"""
    day_map = {}
    for b in all_bars:
        d = str(b["time"])[:10].replace("-", "")
        if d >= trade_day:
            continue
        day_map.setdefault(d, []).append(b)
    days = sorted(day_map)[-lookback:]
    acc = {}
    for d in days:
        for b in day_map[d]:
            hm = str(b["time"])[11:16]
            acc.setdefault(hm, []).append(float(b["vol"]))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def dip_prev_low(bars_up_to, trade_day: str, tol: float) -> bool:
    by_day = {}
    for b in bars_up_to:
        by_day.setdefault(str(b["time"])[:10].replace("-", ""), []).append(b)
    days = sorted(by_day)
    if trade_day not in days or len(days) < 2:
        return False
    i = days.index(trade_day)
    if i == 0:
        return False
    today_low = min(float(b["low"]) for b in by_day[trade_day])
    prev_low = min(float(b["low"]) for b in by_day[days[i - 1]])
    return prev_low > 0 and today_low <= prev_low * (1.0 + tol)


def index_m5_dump(idx_bars, tick_time: str) -> float:
    prior = [b for b in idx_bars if b["time"] <= tick_time]
    if len(prior) < 2:
        return 0.0
    c0, c1 = float(prior[-1]["close"]), float(prior[-2]["close"])
    return round((c0 - c1) / c1 * 100, 3) if c1 > 0 else 0.0


def opened_minutes(t: str) -> int:
    """已开盘连续分钟（与生产 `calc_volume_ratio_at` 同一算法）。"""
    hh, mm = int(t[11:13]), int(t[14:16])
    hm = hh * 100 + mm
    if 930 <= hm <= 1130:
        return (hh - 9) * 60 + mm - 30
    if 1300 <= hm <= 1500:
        return 120 + (hh - 13) * 60 + mm
    return 0


def snapshot(symbol, bars_up_to, trade_day, base, pre_close, idx_bars, tol,
             day_tr=None, day_vol=None, avg5_vol=None):
    """`base` = 生产 `benchmark_turnover_profile.same_minute_avg`（近5个已完成交易日
    **换手率**均值）；若为 None 则回退近 5 日换手率均值（从日线缓存取）。"""
    cur = bars_up_to[-1]
    day_bars = [b for b in bars_up_to if str(b["time"])[:10].replace("-", "") == trade_day]
    o, h, l, c = (float(cur["open"]), float(cur["high"]), float(cur["low"]), float(cur["close"]))
    v, amt = float(cur.get("vol") or 0), float(cur.get("amount") or 0)
    cum_v = sum(float(b.get("vol") or 0) for b in day_bars) or 0.0
    cum_a = sum(float(b.get("amount") or 0) for b in day_bars) or 0.0
    # 生产 vol_ratio = [当日累计换手% × (240/已开盘分钟)] / 基准换手%（近5日换手均值）
    #   回测里"当日累计换手"无法直接读（实时字段）→ 用**等价变形**（不需要未来数据）：
    #     累计换手 ≈ 累计量 / 前5日平均日量 × 基准换手   （代入后基准换手约掉）
    #     ⇒ vr ≈ [累计量 / 前5日平均日量] × (240/已开盘分钟)   = 标准"量比"口径
    #   ⚠️ 单位坑：tushare daily.vol 是**手**、分钟 bar 的 vol 是**股** → 日量要 ×100，否则差 100 倍。
    vr = 0.0
    try:
        opened = opened_minutes(str(cur["time"]))
        v5 = float(avg5_vol or 0.0)
        if opened > 0 and v5 > 0:
            vr = round((cum_v / v5) * (240.0 / opened), 3)
    except Exception:
        vr = 0.0
    return {
        "quote": {
            "current": c, "open": o, "high": h, "low": l, "pre_close": pre_close,
            "change_pct": round((c - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0.0,
            "amplitude": round((h - l) / pre_close * 100, 2) if pre_close > 0 else 0.0,
            "vol": v, "amount": amt,
            "average": (cum_a / cum_v) if cum_v > 0 else 0.0,      # 分时均价（生产 quote.average）
            "dip_prev_low": dip_prev_low(bars_up_to, trade_day, tol),
            "near_day_low": (c <= min(float(b["low"]) for b in day_bars) * 1.01) if day_bars else False,
        },
        "vol_ratio": vr,
        "index": {"m5_dump": index_m5_dump(idx_bars, str(cur["time"]))},
    }


# ── 主流程 ────────────────────────────────────────────────
def run_symbol(pack: str, symbol: str, day: str, conds, dip_tol=0.005, cooldown_bars=1):
    """逐 bar 回放 → 触发列表（含首次时间与次数）。"""
    from datetime import datetime as _dt
    import importlib
    t_expr = importlib.import_module("app.services.t_expr")
    engine_cls = None
    try:
        engine_cls = importlib.import_module("app.core.trading.backtest_paper").BacktestPaperEngine
    except Exception:
        pass

    m5 = load_m5(pack, symbol)
    all_bars = [b for d in sorted(m5) for b in m5[d]]
    all_bars.sort(key=lambda b: b["time"])
    if not all_bars:
        return {"status": "no_bars", "triggers": []}
    idx_bars = load_index_m5(pack, "sh")
    daily = load_daily(pack, symbol)
    pre_close = 0.0
    prev_days = [r for r in daily if str(r["trade_date"]) < day]
    if prev_days:
        pre_close = float(prev_days[-1]["close"])
    # 基准换手：优先用**条件里存的生产值**（benchmark_turnover_profile.same_minute_avg），
    # 否则用近 5 个已完成交易日 turnover_rate 均值
    base = None
    for c in conds:
        _p = c.get("benchmark_turnover_profile")
        if isinstance(_p, str):
            try:
                _p = json.loads(_p)
            except Exception:
                _p = None
        if isinstance(_p, dict) and _p.get("same_minute_avg"):
            base = float(_p["same_minute_avg"]); break
    if base is None:
        _trs = [float(r["turnover_rate"]) for r in daily
                if str(r.get("trade_date")) < day and r.get("turnover_rate")]
        base = (sum(_trs[-5:]) / len(_trs[-5:])) if _trs else None
    day_tr = next((float(r["turnover_rate"]) for r in daily
                   if str(r.get("trade_date")) == day and r.get("turnover_rate")), None)
    day_vol = next((float(r["day_vol"]) for r in daily
                    if str(r.get("trade_date")) == day and r.get("day_vol")), None)
    _vols = [float(r["day_vol"]) for r in daily
             if str(r.get("trade_date")) < day and r.get("day_vol")]
    # 日线 vol 单位=手 → ×100 换成股，与分钟 bar 对齐
    avg5_vol = (sum(_vols[-5:]) / len(_vols[-5:]) * 100.0) if _vols else None
    print("[tape] 基准换手=%s；当日换手=%s 当日量=%s；前5日均量(股)=%s"
          % (base, day_tr, day_vol, avg5_vol), flush=True)

    day_bars = [b for b in all_bars if str(b["time"])[:10].replace("-", "") == day]
    if not day_bars:
        return {"status": "no_day_bars", "triggers": []}
    print("[tape] %s %s：当日 %d 根 5min bar，pre_close=%s，基准换手=%s"
          % (symbol, day, len(day_bars), pre_close, base), flush=True)

    prior = [b for b in all_bars if str(b["time"])[:10].replace("-", "") < day]
    trig, last_bar_idx = [], {}
    for i, bar in enumerate(day_bars):
        up_to = prior + day_bars[:i + 1]
        snap = snapshot(symbol, up_to, day, base, pre_close, idx_bars, dip_tol, day_tr, day_vol, avg5_vol)
        for c in conds:
            cid = c.get("trigger_kind")
            if cid in last_bar_idx and i - last_bar_idx[cid] < cooldown_bars:
                continue
            try:
                hit = t_expr.evaluate_expression(c.get("expression"), snap)
            except Exception:
                hit = False
            if hit:
                last_bar_idx[cid] = i
                trig.append({"time": str(bar["time"]), "kind": cid, "price": float(bar["close"]),
                             "vol_ratio": snap["vol_ratio"], "average": round(snap["quote"]["average"], 3),
                             "dip_prev_low": snap["quote"]["dip_prev_low"],
                             "index_m5_dump": snap["index"]["m5_dump"]})
    # 撮合（可选）：按触发顺序用 BacktestPaperEngine 落单（T+1 / 整手 / 涨跌停由其内部处理）
    fills = []
    if engine_cls is not None and trig:
        try:
            eng = engine_cls("bt_tape_%s_%s" % (symbol, day), initial_capital=250000.0)
            eng.set_current_date(_dt.strptime(day, "%Y%m%d").date())
            for t in trig[:6]:                       # 单标的单日最多 6 次（与生产 MAX_DAILY_BUY_LEGS 同量级）
                vol = int(25000 / max(t["price"], 0.01) / 100) * 100
                if vol <= 0:
                    continue
                r = eng.place_order(symbol, "buy", t["price"], vol)
                fills.append(dict(t, order=r.get("message"), ok=r.get("success"), order_id=r.get("order_id")))
        except Exception as e:
            fills = [{"err": str(e)[:120]}]
    return {"status": "ok", "n_bars": len(day_bars), "triggers": trig, "fills": fills}


def conds_from_prod(symbol: str, day: str):
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    cur.execute("""SELECT id, trigger_kind, expression, benchmark_turnover_profile FROM t_conditions
                   WHERE symbol=%s AND trade_date=%s AND publisher='switch' AND direction='buy' ORDER BY id""",
                (symbol, day))
    out = [{"id": int(i), "trigger_kind": k,
            "expression": e if isinstance(e, dict) else json.loads(e or "{}"),
            "benchmark_turnover_profile": b} for i, k, e, b in cur.fetchall()]
    cur.close(); c.close()
    return out


def prod_triggers(symbol: str, day: str):
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    cur.execute("""SELECT t.trigger_kind, min(tr.created_at)::text, count(*)
                   FROM t_triggers tr JOIN t_conditions t ON t.id = tr.condition_id
                   WHERE tr.symbol=%s AND tr.created_at::date = %s::date GROUP BY 1""",
                (symbol, "%s-%s-%s" % (day[:4], day[4:6], day[6:8])))
    out = {str(k): {"first": f, "n": int(n)} for k, f, n in cur.fetchall()}
    cur.close(); c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--pack", default="/app/data/_bt_full/pack")
    ap.add_argument("--dip-tol", type=float, default=0.005)
    ap.add_argument("--conditions-from-prod", action="store_true")
    ap.add_argument("--legs", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    if a.conditions_from_prod:
        conds = conds_from_prod(a.symbol, a.date)
    else:
        import importlib
        arm = importlib.import_module("rotation_switch_arm")
        conds = [{"trigger_kind": "custom_m5dump", "expression": arm.BUY_253_EXPR},
                 {"trigger_kind": "custom_prevlow", "expression": arm.BUY_254_EXPR}]
    if not conds:
        print("[tape] 无条件"); return 2
    res = run_symbol(a.pack, a.symbol, a.date, conds, dip_tol=a.dip_tol)
    print("[tape] 状态=%s 触发 %d 次" % (res["status"], len(res.get("triggers") or [])))
    for t in (res.get("triggers") or [])[:8]:
        print("     ", json.dumps(t, ensure_ascii=False))
    for f in (res.get("fills") or [])[:4]:
        print("     fill:", json.dumps({k: f.get(k) for k in ("time", "kind", "price", "order", "ok")}, ensure_ascii=False))
    p = prod_triggers(a.symbol, a.date)
    if p:
        print("[tape] 生产真实触发：%s" % json.dumps(p, ensure_ascii=False)[:300])
    if a.json:
        json.dump(dict(res, prod=p, symbol=a.symbol, date=a.date, dip_tol=a.dip_tol),
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

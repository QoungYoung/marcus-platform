# -*- coding: utf-8 -*-
"""bt_intraday.py — 分钟级**盘中触发回放**（全拟真回测的 Pass 2 起点）。

复用生产自带的 m5 回放引擎 `backend/app/services/t_backtest.py::TBacktestEngine`
（逐 bar 重建快照 → 条件求值 → 护栏 → 撮合），喂我们自己的分钟缓存（`bt_pack_mins.py` 打包），
再与**生产真实触发**（`t_triggers`，join `t_conditions` 得到 253/254 类型）逐条对账。

用法（容器内）：
  # ① 用生产当天的条件定义（隔离"引擎保真度"与"选股保真度"）
  python jobs/bt_intraday.py --date 20260911 --symbol SH600039 --conditions-from-prod \
      --pack /app/data/_bt_full/pack [--json out.json]
  # ② 用我们 Pass 1 重放出的腿（`_bt_full/<date>/legs*.jsonl`）
  python jobs/bt_intraday.py --date 20260911 --symbol SH600039 \
      --legs /app/data/_bt_full/20260911/legs_switch.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path[:0] = ["/app", "/app/backend"]


def install_leg_fields(TB, pack: str, symbol: str, dip_tol: float):
    """给引擎的快照补上**两个 253/254 专用字段**（引擎原本只服务做T账户，没有这两个）：

    · `quote.dip_prev_low` = 当日（截至该 bar）最低 ≤ **前一交易日**最低 × (1+tol)
      —— 与生产 `t_monitor._stock_dip_prev_low()` 同口径（它用 5min 序列按日前 8 位分组取 min low）；
    · `index.m5_dump`       = 指数 5min **单根跌幅%**（最近两根指数 bar 的收盘）
      —— 与生产 `t_monitor._index_m5_dump()` 同口径（它取腾讯 m5 最近 60 根的最后两根）。
    做法：包一层 `build_snapshot_at`，不改生产文件（回测专用注入）。
    """
    import json as _json
    import os as _os
    from datetime import datetime as _dt

    idx_path = _os.path.join(pack, "index_m5", "sh.json")
    idx_bars = []
    if _os.path.exists(idx_path):
        d = _json.load(open(idx_path, encoding="utf-8"))
        for day_bars in d.values():
            idx_bars.extend(day_bars)
        idx_bars.sort(key=lambda b: b["time"])

    def _dump_at(tick_time: str) -> float:
        prior = [b for b in idx_bars if b["time"] <= tick_time]
        if len(prior) < 2:
            return 0.0
        c0, c1 = float(prior[-1]["close"]), float(prior[-2]["close"])
        return round((c0 - c1) / c1 * 100, 3) if c1 > 0 else 0.0

    def _dip_prev_low(bars_up_to, trade_day: str) -> bool:
        by_day = {}
        for b in bars_up_to:
            by_day.setdefault(str(b["time"])[:10].replace("-", ""), []).append(b)
        days = sorted(by_day)
        if trade_day not in days or len(days) < 2:
            return False
        today_low = min(float(b["low"]) for b in by_day[trade_day])
        prev_low = min(float(b["low"]) for b in by_day[days[days.index(trade_day) - 1]])
        return prev_low > 0 and today_low <= prev_low * (1.0 + dip_tol)

    orig = TB.build_snapshot_at

    def _build(symbol_, bars_up_to, trade_day, regime, ledger, vol_ratio_base, pre_close):
        snap = orig(symbol_, bars_up_to, trade_day, regime, ledger, vol_ratio_base, pre_close)
        try:
            snap["quote"]["dip_prev_low"] = _dip_prev_low(bars_up_to, trade_day)
            snap["index"]["m5_dump"] = _dump_at(str(bars_up_to[-1]["time"]))
        except Exception:
            pass
        return snap

    TB.build_snapshot_at = _build
    print("[intraday] 已注入 quote.dip_prev_low（tol=%s）与 index.m5_dump" % dip_tol, flush=True)


def conds_from_prod(symbol: str, day: str):
    """从生产 `t_conditions` 取该标的该日的 253/254 条件定义（expression 原样）。"""
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    cur.execute("""SELECT id, trigger_kind, expression, direction, trade_date FROM t_conditions
                   WHERE symbol=%s AND trade_date=%s AND publisher='switch' AND direction='buy'
                   ORDER BY id""", (symbol, day))
    out = []
    for cid, kind, expr, direction, td in cur.fetchall():
        out.append({"id": int(cid), "trigger_kind": kind,
                    "expression": expr if isinstance(expr, dict) else json.loads(expr or "{}"),
                    "direction": direction, "trade_date": str(td), "armed": 1})
    cur.close(); c.close()
    return out


def conds_from_legs(symbol: str, legs_path: str):
    """用我们重放的腿构造条件（253/254 表达式与生产同源：从当前代码常量取）。"""
    legs = []
    if os.path.exists(legs_path):
        for ln in open(legs_path, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    legs.append(json.loads(ln))
                except Exception:
                    pass
    if not any(l.get("symbol") == symbol for l in legs):
        return []
    import importlib
    arm = importlib.import_module("rotation_switch_arm")
    return [{"id": 1, "trigger_kind": "custom_m5dump", "expression": arm.BUY_253_EXPR,
             "direction": "buy", "armed": 1},
            {"id": 2, "trigger_kind": "custom_prevlow", "expression": arm.BUY_254_EXPR,
             "direction": "buy", "armed": 1}]


def prod_triggers(symbol: str, day: str):
    """生产真实触发（按 kind 归并，给首次时间与次数）。"""
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    cur.execute("""SELECT t.trigger_kind, min(tr.created_at), count(*)
                   FROM t_triggers tr JOIN t_conditions t ON t.id = tr.condition_id
                   WHERE tr.symbol=%s AND tr.created_at::date = %s::date
                   GROUP BY 1 ORDER BY 1""",
                (symbol, "%s-%s-%s" % (day[:4], day[4:6], day[6:8])))
    out = {str(k): {"first": str(f), "n": int(n)} for k, f, n in cur.fetchall()}
    cur.close(); c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--pack", default="/app/data/_bt_full/pack")
    ap.add_argument("--conditions-from-prod", action="store_true")
    ap.add_argument("--legs", default="")
    ap.add_argument("--net-asset", type=float, default=250000.0)
    ap.add_argument("--init-price", type=float, default=0.0)
    ap.add_argument("--fee-rate", type=float, default=0.000396, help="买侧单边费率（默认现行口径）")
    ap.add_argument("--dip-tol", type=float, default=0.005,
                    help="254 触发容差（生产 09-15 前为 0.005 自设值；语料值 0.0）")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    conds = conds_from_prod(a.symbol, a.date) if a.conditions_from_prod else conds_from_legs(a.symbol, a.legs)
    if not conds:
        print("[intraday] 无可用条件（symbol=%s date=%s）" % (a.symbol, a.date)); return 2
    print("[intraday] %s %s：条件 %d 条 → %s"
          % (a.symbol, a.date, len(conds), [c["trigger_kind"] for c in conds]), flush=True)

    import importlib
    TB = importlib.import_module("app.services.t_backtest")
    # 费用口径：引擎默认 0.0005 单边（旧近似）→ 用现行 0.1292%/往返 的单边拆分
    TB.DEFAULT_FEE_RATE = a.fee_rate
    task = {"symbol": a.symbol, "conditions": conds, "init_shares": 0,
            "init_price": a.init_price, "net_asset": a.net_asset,
            "start_trade_day": a.date, "end_trade_day": a.date}
    install_leg_fields(TB, a.pack, a.symbol, a.dip_tol)
    eng = TB.TBacktestEngine(task, a.pack, fee_rate=a.fee_rate)
    res = eng.run()
    evs = (res or {}).get("events") or []
    trig = [e for e in evs if str(e.get("type") or "").lower() in ("trigger", "condition_trigger", "buy")]
    print("[intraday] 引擎状态=%s 事件=%d 触发类=%d" % ((res or {}).get("status"), len(evs), len(trig)), flush=True)
    for e in trig[:12]:
        print("    ", json.dumps({k: e.get(k) for k in ("time", "type", "kind", "price", "volume", "reason")},
                                 ensure_ascii=False)[:200])
    p = prod_triggers(a.symbol, a.date) if a.conditions_from_prod else {}
    if p:
        print("[intraday] 生产真实触发：%s" % json.dumps(p, ensure_ascii=False)[:300])
    if a.json:
        json.dump({"symbol": a.symbol, "date": a.date, "status": (res or {}).get("status"),
                   "engine_triggers": trig, "prod_triggers": p, "n_events": len(evs)},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[intraday] 写出", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

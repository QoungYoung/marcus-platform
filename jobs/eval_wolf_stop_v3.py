# -*- coding: utf-8 -*-
"""eval_wolf_stop_v3.py — v3 统一止损层的第一版回测（指数级）。

口径（全部标注）：
  · 数据：data/index_daily_000001.json（2025-01-02→2026-09-01，OHLC+vol）
  · 信号：收盘出信号，**次日收盘**执行（D+1 close），成本 0.1%/边
  · v3：`wolf_stop_layer.evaluate()`；点位用上一交易日算（PIT）
  · down-wave 代理：收盘 < MA60 且 MA60 下行（无历史 wave_state，代理，不是原话）
  · 计算位破位：PointEngine 关键位 + 收盘确认 → tier side = 50%
  · 个股不在本回测（指数级）
臂：
  hold            100% 持有
  down30          仅 down 代理 → 30%
  v3_break        仅计算位破位 → 50%
  v3_break_down   down → 30%；否则计算位破位 → 50%
  v3_down_only    down → 30%；不跑计算位（对照）
输出：docs/wolf-stop-v3-backtest.md
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import wolf_stop_layer as WL  # noqa: E402

COST = 0.001


def load_bars():
    p = os.path.join(ROOT, "data", "index_daily_000001.json")
    d = json.load(open(p, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    out = []
    for r in d:
        out.append({"trade_date": str(r["trade_date"]), "open": float(r["open"]),
                    "high": float(r["high"]), "low": float(r["low"]),
                    "close": float(r["close"]), "vol": float(r.get("vol") or 0)})
    return out


def ma(bars, n, i):
    if i + 1 < n:
        return None
    return sum(b["close"] for b in bars[i + 1 - n:i + 1]) / n


def down_proxy(bars, i):
    m = ma(bars, 60, i)
    mp = ma(bars, 60, i - 1) if i > 0 else None
    return bool(m and mp and bars[i]["close"] < m and m < mp)


def run_arm(bars, arm):
    exp = 1.0
    state = 1.0
    eq = 1.0
    peak = 1.0
    dd = 0.0
    rets = []
    switches = 0
    events = []
    exp_sum = 0.0
    n = 0
    for i in range(60, len(bars) - 1):
        target = state
        down = down_proxy(bars, i)
        close = bars[i]["close"]
        m20 = ma(bars, 20, i)
        m60 = ma(bars, 60, i)
        if arm == "hold":
            target = 1.0
        elif arm in ("down30", "v3_down_only"):
            if down:
                target = 0.30
            elif state < 1.0 and m60 and close > m60:
                target = 1.0
        elif arm in ("v3_break", "v3_break_down"):
            if arm == "v3_break_down" and down:
                target = 0.30
            else:
                wave = {"level": "d4", "operation": "side"}
                ctx = WL.StopContext(symbol="000001.SH", bars=bars[:i + 1], wave=wave,
                                     operation="side", phase="close", time_ok=True,
                                     position={"was_broken": False})
                dec = WL.evaluate(ctx, target_fn=lambda op: 0.50)
                if dec.action == "reduce_to" and dec.target_pct is not None:
                    target = float(dec.target_pct)
                    events.append({"date": bars[i]["trade_date"], "close": close,
                                   "target": target, "level_type": (dec.level or {}).get("type"),
                                   "level_price": (dec.level or {}).get("price"),
                                   "reason": dec.reason})
                elif state < 1.0 and m20 and close > m20:
                    target = 1.0
        target = max(0.0, min(1.0, target))
        if abs(target - exp) > 1e-9:
            switches += 1
        r = bars[i + 1]["close"] / bars[i]["close"] - 1.0
        eq *= (1.0 + target * r - COST * abs(target - exp))
        exp = target
        state = target
        exp_sum += exp
        n += 1
        rets.append(target * r - COST * abs(target - exp))
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1.0)
    avg = sum(rets) / len(rets) if rets else 0.0
    sd = math.sqrt(sum((x - avg) ** 2 for x in rets) / len(rets)) if len(rets) > 1 else 0.0
    sharpe = (avg / sd * math.sqrt(252)) if sd > 0 else 0.0
    return {"arm": arm, "total": eq - 1.0, "mdd": dd, "sharpe": sharpe,
            "avg_exp": exp_sum / n if n else 0.0, "switches": switches, "days": n,
            "events": events}

def main():
    bars = load_bars()
    arms = ["hold", "down30", "v3_break", "v3_break_down", "v3_down_only"]
    rows = [run_arm(bars, a) for a in arms]
    lines = ["# v3 统一止损层回测（指数级，第一版）", "",
             "- 数据：`data/index_daily_000001.json`，%s→%s，%d 根" % (
                 bars[0]["trade_date"], bars[-1]["trade_date"], len(bars)),
             "- 执行：收盘信号 → **次日收盘**；成本 0.1%/边",
             "- down-wave：**代理** = 收盘<MA60 且 MA60 下行（无历史 wave_state，非原话）",
             "- 计算位破位：PointEngine 关键位 + 收盘确认 → tier side 50%",
             "- 个股：不在本回测（只测指数级）", "",
             "| 臂 | 总收益 | 最大回撤 | Sharpe | 平均仓位 | 换手次数 |",
             "|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append("| %s | %+.2f%% | %.2f%% | %.2f | %.0f%% | %d |" % (
            r["arm"], r["total"] * 100, r["mdd"] * 100, r["sharpe"],
            r["avg_exp"] * 100, r["switches"]))
    lines += ["", "> ⚠️ down-wave 是 MA60 代理，不是 `wave_state` 原话；计算位破位的 tier 目标用 side=50%（外推）。",
              "> 结论只能作为“v3 机制在指数上的方向性对照”，不能当成“狼大规则已验证”。"]
    for r in rows:
        if r["arm"] in ("v3_break", "v3_break_down"):
            print("\n[%s] events=%d" % (r["arm"], len(r["events"])))
            for e in r["events"]:
                print("%s close=%.2f target=%.2f key=%s@%s reason=%s" % (
                    e["date"], e["close"], e["target"], e["level_type"],
                    e["level_price"], str(e["reason"])[:80]))
    out = os.path.join(ROOT, "docs", "wolf-stop-v3-backtest.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

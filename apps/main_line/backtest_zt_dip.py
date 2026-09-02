# -*- coding: utf-8 -*-
"""正T买点回测：大盘盘中跳水 → 个股低吸 → T+1/T+2/T+3 卖出（纯规则）
信号（规则化狼大1-12『利用盘中大盘带下来的机会做正T』）：
  A: 指数5min 当日高点回撤 >= X% （盘中跳水）
  B: 指数5min 单根跌幅 >= Y% （急跌）
  C: A 且 个股当日也下跌（被大盘带下来）
数据: index_5min_dh.json(指数5min) + stock_5min_*.json(个股5min聚合日线)
输出: data/zt_dip_backtest.json
"""
import json, os
import pandas as pd, numpy as np

DATA = os.environ.get("DATA_DIR", "data")
idx5_raw = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
idx5 = {k.replace("-", ""): v for k, v in idx5_raw.items()}

def agg_daily(bars):
    """5min bars -> 日线 {date: {open, close, high, low}}（bars 升序）"""
    if not bars: return None
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    return {
        "open": float(bars[0]["open"]),
        "close": float(bars[-1]["close"]),
        "high": max(float(b["high"]) for b in bars),
        "low": min(float(b["low"]) for b in bars),
    }

def idx_dip_signal(bars, dd_th, bar_drop_th):
    """返回 (是否跳水, 确认时点close) — 盘中扫描：当日高点回撤或单根急跌"""
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    if len(closes) < 10: return False, None
    day_high = highs[0]
    for i in range(1, len(closes)):
        day_high = max(day_high, highs[i])
        dd = (day_high - closes[i]) / day_high * 100 if day_high > 0 else 0
        drop1 = (closes[i] / closes[i-1] - 1) * 100 if i >= 1 else 0
        if dd >= dd_th or (bar_drop_th and drop1 <= -bar_drop_th):
            return True, closes[i]
    return False, None

stocks = ["603259", "603678"]
stock_daily = {}
for code in stocks:
    m5 = json.load(open(os.path.join(DATA, "stock_5min_%s.json" % code), encoding="utf-8"))
    d = {}
    for ds, bars in m5.items():
        agg = agg_daily(bars)
        if agg: d[ds] = agg
    stock_daily[code] = d

def fwd_close(daily, dt, n):
    """dt 之后第 n 个交易日的收盘价（个股日线，日期字符串）"""
    keys = sorted(daily.keys())
    if dt not in keys: return None
    i = keys.index(dt)
    if i + n >= len(keys): return None
    return float(daily[keys[i+n]]["close"]), keys[i+n]

# 回测：X(回撤%) x 组合
variants = [
    ("A_dd0.8", 0.8, None, False),
    ("A_dd1.0", 1.0, None, False),
    ("A_dd1.5", 1.5, None, False),
    ("A_dd2.0", 2.0, None, False),
    ("B_drop0.5", 99.0, 0.5, False),
    ("B_drop0.8", 99.0, 0.8, False),
    ("C_dd1.0_stkdn", 1.0, None, True),
    ("C_dd1.5_stkdn", 1.5, None, True),
]

results = {}
all_days = sorted(stock_daily[stocks[0]].keys())
# 基线：全部交易日买入
base = {"1": [], "2": [], "3": []}
for code in stocks:
    daily = stock_daily[code]
    for dt in all_days:
        if dt not in daily: continue
        for n in (1, 2, 3):
            r = fwd_close(daily, dt, n)
            if r: base[str(n)].append((float(daily[dt]["close"]) / r[0] - 1) * -100 if False else (r[0] / float(daily[dt]["close"]) - 1) * 100)

print("== 正T 大盘跳水低吸回测（个股日线, %d交易日, 两只合并）==" % len(all_days))
print("基线(全交易日买入, 合并): " + " | ".join(f"T+{k} n={len(v)} mean={np.mean(v):+.2f}% hit={np.mean(np.array(v)>0):.2f}" for k, v in base.items() if v))
print()

for name, dd_th, drop_th, stk_dn in variants:
    g = {"1": [], "2": [], "3": []}
    n_sig = 0
    for code in stocks:
        daily = stock_daily[code]
        for dt in all_days:
            if dt not in idx5 or dt not in daily: continue
            sig, _ = idx_dip_signal(idx5[dt], dd_th, drop_th)
            if not sig: continue
            if stk_dn:  # 个股当日也跌（被带下来）
                d = daily[dt]
                if d["close"] >= d["open"]: continue
            n_sig += 1
            for n in (1, 2, 3):
                r = fwd_close(daily, dt, n)
                if r: g[str(n)].append((r[0] / float(daily[dt]["close"]) - 1) * 100)
    parts = []
    for k in ("1", "2", "3"):
        v = g[k]
        if v: parts.append(f"T+{k} n={len(v)} mean={np.mean(v):+.2f}% hit={np.mean(np.array(v)>0):.2f}")
    print(f"{name:<16} (信号{n_sig}天): " + " | ".join(parts))
    results[name] = {"n_sig": n_sig, "groups": g}

results["base"] = {"groups": base}
json.dump(results, open(os.path.join(DATA, "zt_dip_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/zt_dip_backtest.json")
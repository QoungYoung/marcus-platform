# -*- coding: utf-8 -*-
"""正T 大盘跳水低吸 v2：5只股票 + 盘中口径买入 + dd∈[2,3) 过滤"""
import json, os
import pandas as pd, numpy as np

DATA = os.environ.get("DATA_DIR", "data")
idx5_raw = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
idx5 = {k.replace("-", ""): v for k, v in idx5_raw.items()}

def agg_daily(bars):
    if not bars: return None
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    return {"open": float(bars[0]["open"]), "close": float(bars[-1]["close"]),
            "high": max(float(b["high"]) for b in bars), "low": min(float(b["low"]) for b in bars)}

def idx_dip_info(bars):
    """返回 (max_dd, 确认时点close, 确认bar时间) — dd>=2% 时确认时点"""
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    if len(closes) < 10: return 0.0, None, None
    dh = highs[0]; mdd = 0.0; conf = None; conf_t = None
    for i in range(1, len(closes)):
        dh = max(dh, highs[i])
        dd = (dh - closes[i]) / dh * 100 if dh > 0 else 0
        if dd >= 2.0 and conf is None:
            conf = closes[i]; conf_t = str(bars[i].get("trade_time") or bars[i].get("time"))
        mdd = max(mdd, dd)
    return mdd, conf, conf_t

STOCKS = ["603259", "603678", "000725", "002384", "688072"]
stock_daily = {}; stock_bars = {}
for code in STOCKS:
    m5 = json.load(open(os.path.join(DATA, "stock_5min_%s.json" % code), encoding="utf-8"))
    stock_bars[code] = m5
    d = {}
    for ds, bars in m5.items():
        agg = agg_daily(bars)
        if agg: d[ds] = agg
    stock_daily[code] = d

all_days = sorted(stock_daily[STOCKS[0]].keys())
half = len(all_days) // 2

def fwd_close(daily, dt, n):
    keys = sorted(daily.keys())
    if dt not in keys: return None
    i = keys.index(dt)
    if i + n >= len(keys): return None
    return float(daily[keys[i+n]]["close"])

# 收集信号日 + 确认时点
sig_days = {}
for dt in all_days:
    if dt not in idx5: continue
    mdd, conf, conf_t = idx_dip_info(idx5[dt])
    if 2.0 <= mdd < 3.0:
        sig_days[dt] = (mdd, conf, conf_t)

print("信号日(%d):" % len(sig_days), sorted(sig_days.keys()))
print()

# 口径1: 收盘买入；口径2: 跳水确认时点后 下一根个股bar 买入（盘中）
for mode in ("close", "intraday"):
    print(f"== 口径{mode} ==")
    for seg in ("all", "h1", "h2"):
        g = {"1": [], "2": [], "3": []}; n_stk = 0
        for code in STOCKS:
            daily = stock_daily[code]
            for dt, (mdd, conf, conf_t) in sig_days.items():
                if seg == "h1" and all_days.index(dt) >= half: continue
                if seg == "h2" and all_days.index(dt) < half: continue
                if dt not in daily: continue
                buy_px = float(daily[dt]["close"])
                if mode == "intraday":
                    # 跳水确认后个股下一根5min bar 收盘价（同日盘中买入）
                    bars = stock_bars[code].get(dt) or []
                    if not bars: continue
                    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
                    conf_hhmm = str(conf_t or "")[11:16]
                    nxt = None
                    for b in bars:
                        bt = str(b.get("trade_time") or b.get("time"))[11:16]
                        if bt > conf_hhmm: nxt = float(b["close"]); break
                    if nxt is None or nxt <= 0: continue
                    buy_px = nxt
                n_stk += 1
                for n in (1, 2, 3):
                    r = fwd_close(daily, dt, n)
                    if r: g[str(n)].append((r / buy_px - 1) * 100)
        parts = []
        for k in ("1", "2", "3"):
            v = g[k]
            if v: parts.append(f"T+{k} n={len(v)} mean={np.mean(v):+.2f}% hit={np.mean(np.array(v)>0):.2f}")
        print(f"  {seg:<4} (标的-日{n_stk}): " + " | ".join(parts))
    print()

# 各股拆分（口径 close, dd>=2）
print("== 个股拆分 (收盘买入, dd>=2%) ==")
for code in STOCKS:
    daily = stock_daily[code]
    g = {"1": [], "2": [], "3": []}
    for dt in sig_days:
        if dt not in daily: continue
        buy_px = float(daily[dt]["close"])
        for n in (1, 2):
            r = fwd_close(daily, dt, n)
            if r: g[str(n)].append((r / buy_px - 1) * 100)
    if g["1"]:
        print(f"  {code}: T+1 n={len(g['1'])} mean={np.mean(g['1']):+.2f}% hit={np.mean(np.array(g['1'])>0):.2f} | T+2 mean={np.mean(g['2']):+.2f}% hit={np.mean(np.array(g['2'])>0):.2f}")

json.dump({"sig_days": {k: [round(v[0],2), v[1], v[2]] for k, v in sig_days.items()}}, open(os.path.join(DATA, "zt_dip_v2_detail.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/zt_dip_v2_detail.json")
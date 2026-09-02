# -*- coding: utf-8 -*-
"""做T信号回测(指数级, 真实vol): T1缩转放买点 / T_sell T出点 前向收益
数据: data/index_daily_000001.json (close+vol)
方法: 每20交易日时点, t_signal 状态 → 分组前向 5/20日
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t_signal import t_signal

DATA = os.environ.get("DATA_DIR", "data")
rows = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(rows); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)
vol = df.set_index("trade_date")["vol"].astype(float)

def fwd(s, upto, n):
    base = s[s.index <= upto]; fut = s[s.index > upto]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    return (float(fut.iloc[n-1]) / b0 - 1) * 100 if b0 > 0 else None

# 逐日评估 T 信号(每日都可判), 统计触发后前向
grp_buy = {"5": [], "20": []}; grp_sell = {"5": [], "20": []}; grp_none = {"5": [], "20": []}
n_buy = n_sell = n_none = 0
for i in range(120, len(df) - 20):
    upto = df["trade_date"].iloc[i]
    s = ser[ser.index <= upto]; v = vol[vol.index <= upto]
    sig = t_signal(s, v)
    for n in (5, 20):
        r = fwd(ser, upto, n)
        if r is None: continue
        if sig["T_sell"]: grp_sell[str(n)].append(r)
        elif sig["T1_缩转放"]: grp_buy[str(n)].append(r)
        else: grp_none[str(n)].append(r)
    if sig["T_sell"]: n_sell += 1
    elif sig["T1_缩转放"]: n_buy += 1
    else: n_none += 1

def show(name, g, n):
    if not g: print(f"  {name}: n=0"); return
    for k in ("5", "20"):
        v = g[k]
        if v: print(f"  {name}({k}日): n={len(v)} mean={np.mean(v):.2f}% hit={np.mean(np.array(v)>0):.2f}", end=" | ")
    print(f"(触发{n}次)")

print("== 指数做T信号 (2025-12~2026-08) ==")
show("T1缩转放买点", grp_buy, n_buy)
show("T_sell T出点", grp_sell, n_sell)
show("无信号", grp_none, n_none)
print()
print(f"触发率: 缩转放 {n_buy}, T出 {n_sell}, 无 {n_none}")
json.dump({"grp_buy": grp_buy, "grp_sell": grp_sell, "grp_none": grp_none},
          open(os.path.join(DATA, "t_signal_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/t_signal_backtest.json")

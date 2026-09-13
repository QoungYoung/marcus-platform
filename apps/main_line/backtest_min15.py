# -*- coding: utf-8 -*-
"""15分钟级站稳验证: 当日15min站稳(3根≥中轨) vs 次日指数收益
数据: data/index_15min_000001.json + data/index_daily_000001.json
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from confirm_chain import min15_stand

DATA = os.environ.get("DATA_DIR", "data")
m15 = json.load(open(os.path.join(DATA, "index_15min_000001.json"), encoding="utf-8"))
rows = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(rows); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)

# 对每个有15min数据的日期: 跨日拼接(前2天+当天)算站稳(狼大BOLL中轨≈20根15min跨日) → 次日/次5日收益
days_sorted = sorted(m15.keys())
all_bars = []   # (date, bar) 有序拼接
for ds in days_sorted:
    for b in m15[ds]:
        all_bars.append((ds, b))
grp_ok = []; grp_no = []; details = []
for di, ds in enumerate(days_sorted):
    bars = m15[ds]
    if not bars: continue
    # 前2天+当天 的 15min 收盘序列
    idx = [i for i, (d, b) in enumerate(all_bars) if d == ds]
    if not idx: continue
    start = max(0, idx[0] - 34)   # 前2天约34根 + 当天17根 = 51根
    win = [all_bars[i][1] for i in range(start, idx[-1] + 1)]
    stand = min15_stand(win, look=20, stand_n=3) if len(win) >= 23 else False
    dt = pd.Timestamp(ds)
    if dt not in ser.index: continue
    i0 = df.index[df["trade_date"] == dt][0]
    if i0 + 1 >= len(df): continue
    r1 = (float(ser.iloc[i0+1]) / float(ser.iloc[i0]) - 1) * 100
    r5 = (float(ser.iloc[min(i0+5, len(df)-1)]) / float(ser.iloc[i0]) - 1) * 100
    (grp_ok if stand else grp_no).append((r1, r5))
    details.append({"date": ds, "stand": stand, "r1": round(r1,2), "r5": round(r5,2)})

def show(name, v):
    if not v: print(f"  {name}: n=0"); return
    r1 = [x[0] for x in v]; r5 = [x[1] for x in v]
    print(f"  {name}: n={len(v)} 次日mean={np.mean(r1):.2f}% hit={np.mean(np.array(r1)>0):.2f} | 5日mean={np.mean(r5):.2f}% hit={np.mean(np.array(r5)>0):.2f}")

print(f"15min 数据天数: {len(m15)}")
print("== 15min站稳 vs 未站稳 (次日/5日指数收益) ==")
show("站稳(3根≥中轨)", grp_ok)
show("未站稳", grp_no)
print()
for d in details[-15:]:
    print(f"  {d['date']} {'站稳' if d['stand'] else '未站稳'} 次日{d['r1']:+.1f}% 5日{d['r5']:+.1f}%")
json.dump({"details": details}, open(os.path.join(DATA, "min15_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/min15_backtest.json")

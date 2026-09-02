# -*- coding: utf-8 -*-
"""②指数级确认链回测: 上证指数 确认链触发 vs 未触发 前向收益
数据: data/index_daily_000001.json (2025-01~2026-09, close+vol+amount)
方法: 每20交易日一个时点, 从时点起扫30日找 confirm_chain 触发(确认/突破候选), 分组对比 20/60日前向
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from confirm_chain import confirm_chain

DATA = os.environ.get("DATA_DIR", "data")
rows = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(rows)
df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)
vol = df.set_index("trade_date")["vol"].astype(float)
amount = df.set_index("trade_date")["amount"].astype(float)

def fwd(s, upto, n):
    base = s[s.index <= upto]; fut = s[s.index > upto]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    return (float(fut.iloc[n-1]) / b0 - 1) * 100 if b0 > 0 else None

# 时点: 2025-12-01 起每20交易日, 留60日前向
dates = []
for i in range(len(df)):
    d = df["trade_date"].iloc[i]
    if d < pd.Timestamp("2025-12-01"): continue
    if len(df) - i <= 65: break
    if not dates or i - df.index[df["trade_date"] == dates[-1]][0] >= 10:
        dates.append(d)

grpA = {"20": [], "60": []}; grpB = {"20": [], "60": []}; details = []
for dt in dates:
    i0 = df.index[df["trade_date"] == dt][0]
    found = None
    for k in range(min(30, len(df) - i0)):
        upto = df["trade_date"].iloc[i0 + k]
        cc = confirm_chain(ser[ser.index <= upto], None, vol[vol.index <= upto])
        if cc["stage"] in ("确认", "突破候选"):
            found = (upto, cc["stage"], k); break
    for n in (20, 60):
        if found:
            r = fwd(ser, found[0], n)
            if r is not None: grpA[str(n)].append(r)
        else:
            r = fwd(ser, dt, n)
            if r is not None: grpB[str(n)].append(r)
    details.append({"date": str(dt.date()), "confirmed": bool(found),
                    "stage": found[1] if found else None, "days": found[2] if found else None})

print(f"指数时点: {len(dates)} | 确认链触发: {sum(1 for d in details if d['confirmed'])} ({100*sum(1 for d in details if d['confirmed'])/len(dates):.0f}%)")
print()
print("== 指数确认组(从确认日算) vs 未确认组(从时点算) ==")
for n in (20, 60):
    a = grpA[str(n)]; b = grpB[str(n)]
    fa = f"n={len(a)} mean={np.mean(a):.2f}% hit={np.mean(np.array(a)>0):.2f}" if a else "n=0"
    fb = f"n={len(b)} mean={np.mean(b):.2f}% hit={np.mean(np.array(b)>0):.2f}" if b else "n=0"
    print(f"  {n}日: 确认组 {fa} | 未确认组 {fb}")
print()
print("== 时点明细 ==")
for d in details:
    print(f"  {d['date']} {'✓' if d['confirmed'] else '✗'} {d['stage'] or '未确认'} ({d['days']}日后触发)" if d['confirmed'] else f"  {d['date']} ✗ 未确认")
json.dump({"grpA": grpA, "grpB": grpB, "details": details},
          open(os.path.join(DATA, "confirm_index_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/confirm_index_backtest.json")

# -*- coding: utf-8 -*-
"""分时T出检测(15min粗粒度): 放量反弹→第一次分时高点→停量→第二次拉升无量不过前高→T出
数据: data/index_15min_000001.json (20天) + data/index_daily_000001.json
验证: T出触发后 当日剩余时间/次日表现 (T出正确=触发后滞涨/回落)
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA = os.environ.get("DATA_DIR", "data")
m15 = json.load(open(os.path.join(DATA, "index_15min_000001.json"), encoding="utf-8"))
rows = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(rows); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)

def intraday_t_sell(bars, look=5):
    """15min 分时T出: 
    放量反弹(vol放大) → 第一次分时高点(反弹段局部max) → 高点后停量(后look根均量<前look根0.8)
    → 第二次拉升(价格再冲高) 无量且不过前高*1.005 → T出触发
    bars: [{trade_time, close, vol, amount}] (一天)
    返回 (triggered, desc)"""
    closes = [float(b["close"]) for b in bars]
    vols = [float(b["vol"]) for b in bars]
    n = len(closes)
    if n < look * 4 + 4: return False, "数据不足"
    # 找放量反弹起点: 第一个 vol 放大的点(>前look均值1.3)
    start = None
    for i in range(look, n - look - 2):
        base = np.mean(vols[max(0, i-look):i])
        if base > 0 and vols[i] > base * 1.3:
            start = i; break
    if start is None: return False, "无放量反弹"
    # 第一次分时高点: 从start到n-look的局部max(5根窗口)
    seg = closes[start:n-look]
    hi = max(seg); hi_idx = start + seg.index(hi)
    if hi_idx < start + 2: return False, "高点太近"
    # 高点后停量
    vol_after = np.mean(vols[hi_idx+1:hi_idx+1+look]) if hi_idx+look < n else 0
    vol_before = np.mean(vols[max(start, hi_idx-look):hi_idx])
    stop_vol = vol_after < vol_before * 0.8 if vol_before > 0 else False
    if not stop_vol: return False, "高点后未停量"
    # 第二次拉升: 高点后再次冲高(close 接近前高 98%) 但不过前高
    after = closes[hi_idx+1:]
    second_hi = max(after) if after else 0
    second_up = second_hi > hi * 0.98 and second_hi < hi * 1.005
    return bool(second_up), f"第一高点{hi:.0f} 第二高点{second_hi:.0f}"

grp_t = []; grp_nt = []
for ds, bars in sorted(m15.items()):
    if not bars: continue
    trig, desc = intraday_t_sell(bars)
    dt = pd.Timestamp(ds)
    if dt not in ser.index: continue
    i0 = df.index[df["trade_date"] == dt][0]
    if i0 + 1 >= len(df): continue
    r1 = (float(ser.iloc[i0+1]) / float(ser.iloc[i0]) - 1) * 100
    r3 = (float(ser.iloc[min(i0+3, len(df)-1)]) / float(ser.iloc[i0]) - 1) * 100
    (grp_t if trig else grp_nt).append((r1, r3, desc))

def show(name, v):
    if not v: print(f"  {name}: n=0"); return
    r1 = [x[0] for x in v]; r3 = [x[1] for x in v]
    print(f"  {name}: n={len(v)} 次日mean={np.mean(r1):.2f}% hit={np.mean(np.array(r1)>0):.2f} | 3日mean={np.mean(r3):.2f}%")

print("== 15min 分时T出检测 (20天) ==")
show("T出触发(次日)", grp_t)
show("未触发", grp_nt)
print()
for ds, bars in sorted(m15.items()):
    if not bars: continue
    trig, desc = intraday_t_sell(bars)
    if trig:
        dt = pd.Timestamp(ds)
        i0 = df.index[df["trade_date"] == dt][0]
        r1 = (float(ser.iloc[i0+1]) / float(ser.iloc[i0]) - 1) * 100 if i0+1 < len(df) else None
        print(f"  {ds} T出触发 {desc} | 次日{r1:+.1f}%" if r1 is not None else f"  {ds} T出触发 {desc}")

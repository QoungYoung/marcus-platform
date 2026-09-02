# -*- coding: utf-8 -*-
"""分时T出回测(5min, 184天真实数据): 狼大7-29 T出条件
放量反弹→第一次分时高点→停量→第二次拉升无量不过前高→T出
验证: T出触发后 次日/3日 表现 (T出正确=触发后滞涨/回落 vs 未触发)
数据: data/index_5min_dh.json (datahubco 5min) + index_daily_000001.json
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA = os.environ.get("DATA_DIR", "data")
m5 = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
rows = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(rows); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)

def intraday_t_sell5(bars, look=8):
    """5min 分时T出: 放量反弹(vol>前look均值1.3) → 第一次分时高点(局部max) → 停量(后look均量<前look 0.8)
    → 第二次拉升(再冲高≥前高98%) 无量且不过前高*1.005 → T出"""
    closes = np.array([float(b["close"]) for b in bars])
    vols = np.array([float(b["vol"]) for b in bars])
    n = len(closes)
    if n < look * 4 + 4: return False, "数据不足"
    # 放量反弹起点
    start = None
    for i in range(look, n - look - 2):
        base = vols[max(0, i-look):i].mean()
        if base > 0 and vols[i] > base * 1.3:
            start = i; break
    if start is None: return False, "无放量反弹"
    seg = closes[start:n-look]
    hi = float(seg.max()); hi_idx = start + int(seg.argmax())
    if hi_idx < start + 2: return False, "高点太近"
    vol_after = vols[hi_idx+1:hi_idx+1+look].mean() if hi_idx+look < n else 0
    vol_before = vols[max(start, hi_idx-look):hi_idx].mean()
    if not (vol_before > 0 and vol_after < vol_before * 0.8): return False, "高点后未停量"
    after = closes[hi_idx+1:]
    second_hi = float(after.max()) if len(after) else 0
    second_up = second_hi > hi * 0.98 and second_hi < hi * 1.005
    return bool(second_up), f"第一高点{hi:.0f} 第二高点{second_hi:.0f} 停量后二次拉升"

grp_t = []; grp_nt = []; detail_t = []
for ds, bars in sorted(m5.items()):
    trig, desc = intraday_t_sell5(bars)
    dt = pd.Timestamp(ds)
    if dt not in ser.index: continue
    i0 = df.index[df["trade_date"] == dt][0]
    if i0 + 1 >= len(df): continue
    r1 = (float(ser.iloc[i0+1]) / float(ser.iloc[i0]) - 1) * 100
    r3 = (float(ser.iloc[min(i0+3, len(df)-1)]) / float(ser.iloc[i0]) - 1) * 100
    (grp_t if trig else grp_nt).append((r1, r3))
    if trig: detail_t.append((ds, desc, round(r1,2)))

def show(name, v):
    if not v: print(f"  {name}: n=0"); return
    r1=[x[0] for x in v]; r3=[x[1] for x in v]
    print(f"  {name}: n={len(v)} 次日mean={np.mean(r1):.2f}% hit={np.mean(np.array(r1)>0):.2f} | 3日mean={np.mean(r3):.2f}% hit={np.mean(np.array(r3)>0):.2f}")

print(f"== 5min 分时T出回测 (184天) ==")
show("T出触发(次日从T出日算)", grp_t)
show("未触发", grp_nt)
print()
print(f"T出触发天数: {len(grp_t)}/{len(m5)}")
for ds, desc, r1 in detail_t[:15]:
    print(f"  {ds} {desc} | 次日{r1:+.1f}%")
json.dump({"grp_t": grp_t, "grp_nt": grp_nt, "detail": detail_t},
          open(os.path.join(DATA, "intraday_t5_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/intraday_t5_backtest.json")

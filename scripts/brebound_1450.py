"""14:50 收盘前读数：所有量都用「同刻累计量」比，不用全日量比。

用法: python scripts/brebound_1450.py 20260803 20260804 20260805
"""
from __future__ import annotations
import os, sys, json
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "research", "mins")
CUT = "14:50"

def load_day(code, day):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{code.replace('.','_')}_{day}.json")
    if os.path.exists(p):
        return pd.DataFrame(json.load(open(p)))
    sys.path.insert(0, ROOT)
    from core.tushare_relay import relay_query
    d = relay_query("stk_mins", ts_code=code, freq="1min",
                    start_date=f"{day[:4]}-{day[4:6]}-{day[6:]} 09:30:00",
                    end_date=f"{day[:4]}-{day[4:6]}-{day[6:]} 15:00:00")
    d = d.sort_values("trade_time")
    json.dump(d.to_dict("records"), open(p, "w"))
    return d

def cum(df, hhmm=CUT):
    t = df.trade_time.str[11:16] if "trade_time" in df else None
    sel = df[t <= hhmm]
    if not len(sel): return None
    return dict(vol=sel.vol.sum(), hi=sel.high.max(), lo=sel.low.min(),
                px=sel.close.iloc[-1], n=len(sel),
                tail_vol=sel[t >= "14:30"].vol.sum())  # 14:30-14:50

def read(code, day, prev_days):
    today = cum(load_day(code, day))
    if today is None: return f"{day} 无分钟数据"
    vols = []
    for pd_ in prev_days:
        try:
            c = cum(load_day(code, pd_))
            if c: vols.append(c["vol"])
        except Exception:
            pass
    if not vols: return f"{day} 前 20 日分钟数据不足（需先缓存）"
    v20 = float(np.mean(vols))
    vr = today["vol"]/v20
    pos = (today["px"]-today["lo"])/max(1e-9, today["hi"]-today["lo"])
    tail = today["tail_vol"]/today["vol"]
    return (f"{day}  14:50 价 {today['px']:.3f}  同刻量比 {vr:.2f}×（前{len(vols)}日均值）  "
            f"今日收位 {pos*100:.0f}%  14:30–14:50 占全天已成交 {tail*100:.0f}%")

if __name__ == "__main__":
    import glob
    days = sys.argv[1:] or ["20260803"]
    # 用交易日历取前 20 个交易日
    idx = pd.read_csv(os.path.join(ROOT, "data", "research", "index_daily_000001_long.csv"),
                      dtype={"trade_date": str}).trade_date.tolist()
    for d in days:
        if d not in idx:
            print(f"{d} 非交易日"); continue
        i = idx.index(d)
        prev = idx[max(0, i-20):i]
        print(read("588170.SH", d, prev))

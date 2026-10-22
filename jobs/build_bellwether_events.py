# -*- coding: utf-8 -*-
"""按 docs/bellwether-aligned-spec.md 提取风向标事件/动作。"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "data/pit/chain_map/labels_weekly.csv")
OBS = os.path.join(ROOT, "data/pit/theme_members/observations.csv")
BARS = os.path.join(ROOT, "data/bars_api.parquet")
OUT = os.path.join(ROOT, "data/core_pit")
os.makedirs(OUT, exist_ok=True)

def impulse(close, high, amt, low, code, d, lookback=120):
    """返回 (impulse_date, impulse_high, impulse_low) 或 (None,..)。"""
    if code not in close.columns: return None, None, None
    s = close[code].loc[:d].tail(lookback).dropna()
    if len(s) < 25: return None, None, None
    hi = high[code].reindex(s.index); am = amt[code].reindex(s.index)
    prev = None; idate=None; ihigh=None; ilow=None
    ma20 = am.rolling(20).mean()
    for dt in s.index:
        h = hi.get(dt); a = am.get(dt)
        if pd.isna(h) or pd.isna(a): continue
        vol_ok = pd.notna(ma20.get(dt)) and a >= ma20.get(dt) * 1.2
        if prev is None:
            # 第一个放量新高（相对 20 日高点）
            win = hi.loc[:dt].tail(21)
            new_high = h >= win.max() * 0.999
        else:
            new_high = h > prev * 1.001
        if new_high and vol_ok:
            prev = float(h); idate = str(dt); ihigh = float(h)
            ilow = float(low[code].loc[dt]) if code in low.columns and pd.notna(low[code].loc[dt]) else float(s.loc[dt])
    return idate, ihigh, ilow

def main():
    lab = pd.read_csv(LAB, dtype={"date": str, "ts_code": str})
    obs = pd.read_csv(OBS, dtype={"as_of_date": str, "ts_code": str})
    bars = pd.read_parquet(BARS, columns=["ts_code","trade_date","close","high","low","amount"])
    close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    high = bars.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
    amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    low = bars.pivot_table(index="trade_date", columns="ts_code", values="low", aggfunc="last").sort_index()
    rows=[]
    prev_leader={}
    for d in sorted(lab.date.unique()):
        if d not in close.index: continue
        day=lab[lab.date==d]
        for (theme, seg), g in day.groupby(["theme","segment_role"]):
            lead=g[g["rank"]==1].drop_duplicates("ts_code")
            if not len(lead): continue
            lr=lead.iloc[0]; code=str(lr.ts_code)
            idate, ihigh, ilow = impulse(close, high, amt, low, code, d)
            state="unknown"; reason=""
            leader_ret=np.nan; lag=np.nan; ext=False
            if idate:
                c=close[code].loc[:d].iloc[-1]
                lr_imp=close[code].loc[idate]
                leader_ret=float(c/lr_imp-1) if pd.notna(lr_imp) and lr_imp>0 else np.nan
                ma20=close[code].loc[:d].tail(20).mean()
                ext=bool((leader_ret>=0.12) or (pd.notna(ma20) and c>=ma20*1.15))
                low_imp=ilow if ilow is not None else np.nan
                # 近 5 日放量出货
                a5=amt[code].loc[:d].tail(5).mean(); a20=amt[code].loc[:d].tail(20).mean()
                vol_dist=bool(pd.notna(a20) and a20>0 and a5>=a20*1.2 and c < close[code].loc[:d].iloc[-2])
                dead_line = low_imp*0.98 if pd.notna(low_imp) and low_imp < ihigh*0.98 else ihigh*0.90
                if c < dead_line:
                    state="dead"; reason="跌破impulse低点/10%失效线"
                elif c >= ihigh*0.98:
                    state="alive"
                else:
                    state="warning"; reason="低于impulse高点"
                hi_now = float(high[code].loc[:d].iloc[-1]) if code in high.columns and pd.notna(high[code].loc[:d].iloc[-1]) else np.nan
                if vol_dist and pd.notna(hi_now) and hi_now < ihigh*0.995 and c < ihigh*0.995:
                    state="dead"; reason="放量出货+高点递减"
            # 二线滞后
            sec=g[g.bucket=="second"].drop_duplicates("ts_code")
            sec_codes=[]; sec_lag={}
            for _, s in sec.iterrows():
                sc=str(s.ts_code)
                if idate and sc in close.columns:
                    cs=close[sc].loc[:d].iloc[-1]; ss=close[sc].loc[idate]
                    if pd.notna(cs) and pd.notna(ss) and ss>0 and pd.notna(leader_ret):
                        lagv=float(leader_ret-(cs/ss-1)); sec_lag[sc]=lagv
                        if lagv>=0.05: sec_codes.append(sc)
            lag=max(sec_lag.values()) if sec_lag else np.nan
            catchup = bool(state=="alive" and leader_ret>=0.08 and sec_codes)
            hcl = bool(state=="alive" and ext and sec_codes)
            # 换人：rank1 代码变化
            old=prev_leader.get((theme,seg))
            rotated = bool(old and old!=code)
            prev_leader[(theme,seg)]=code
            rows.append({"date":d,"theme":theme,"segment_role":seg,"leader":code,
                         "state":state,"rotated":rotated,"reason":reason,
                         "impulse_date":idate,"impulse_high":ihigh,"impulse_low":ilow,
                         "leader_ret_since_imp":None if pd.isna(leader_ret) else round(leader_ret,4),
                         "lag":None if pd.isna(lag) else round(float(lag),4),
                         "extended":ext,"second_codes":",".join(sec_codes),
                         "second_catchup":catchup,"high_cut_low":hcl})
    df=pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT,"bellwether_events.csv"),index=False,encoding="utf-8-sig")
    summary={"n_rows":len(df),"dates":int(df.date.nunique()),"themes":int(df.theme.nunique()),
             "state":df.state.value_counts().to_dict(),
             "second_catchup":int(df.second_catchup.sum()),
             "high_cut_low":int(df.high_cut_low.sum()),
             "rotated":int(df.rotated.sum())}
    json.dump(summary,open(os.path.join(OUT,"bellwether_events_summary.json"),"w"),ensure_ascii=False,indent=1)
    print(json.dumps(summary,ensure_ascii=False,indent=1))

if __name__=="__main__":
    main()

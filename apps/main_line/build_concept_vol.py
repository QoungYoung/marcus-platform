# -*- coding: utf-8 -*-
"""
build_concept_vol.py — 概念量能代理(仅资金流入TOP概念)。
流程: concept_hist 每概念近60日龙头股(buy_sm_amount_stock)去重成代表篮子 → stock_basic name→ts_code
      → pro.daily 拉每股 vol → 按日聚合(sum) = 概念量能代理 → 算 vol_pct120/vol_z20/量比。
输出: data/concept_vol.json = { ts_code: {name, dates[], vol[], vol_pct120, vol_z20, ratio, n_leader} }
运行于 worker 容器。
注意(2026-09-01 修复): 原拉取窗口 160 自然日 → 110 交易日 < 120, vol_pct120/vol_z20 从未算出,
      position_class resonance 的 volume_top/volume_bottom 恒为 False。窗口改为 300 自然日
      (~200 交易日), 保证回测时点(2025-12 起)与 vol_pct120 均可算; <120 日时用可用窗口回退并标注 n。
"""
import os, sys, json
sys.path.insert(0,"/app/core")
import pandas as pd, numpy as np
from _api_config import get_tushare_pro
pro=get_tushare_pro()
HIST="/app/data/concept_hist.json"; OUT="/app/data/concept_vol.json"
TOP_N=int(os.getenv("CONCEPT_VOL_TOP","40"))

def main():
    hist=json.load(open(HIST,encoding="utf-8"))
    # 按最新 net_amount 取 top 概念
    def last_net(a):
        na=[x for x in a["net_amount"] if x is not None]
        return na[-1] if na else 0
    top=sorted(hist.items(), key=lambda kv:-(last_net(kv[1]) or 0))[:TOP_N]
    # name->ts_code
    sb=pro.stock_basic(fields="ts_code,name,industry,market", list_status="L")
    nm2code=dict(zip(sb["name"], sb["ts_code"]))
    out={}
    done=0
    for code,a in top:
        # 近60日龙头集合(滚动窗口)
        ds=pd.to_datetime(a["dates"]); ldr=pd.Series(a["leader"], index=ds)
        recent=ldr[ldr.index>=ldr.index[-1]-pd.Timedelta(days=60)].replace("",None).dropna()
        basket=sorted(set(recent.astype(str).tolist()))[:20]
        codes=[nm2code[n] for n in basket if n in nm2code]
        if not codes: out[code]={"name":a["name"],"vol":[],"n_leader":0}; continue
        # 每股 vol, 按日期对齐；窗口 300 自然日(~200 交易日), 保证 ≥120 交易日与历史回测时点可用
        vol_map={}
        start=(pd.Timestamp(a["dates"][-1])-pd.Timedelta(days=300)).strftime("%Y%m%d")
        end=a["dates"][-1]
        for c in codes:
            try:
                d=pro.daily(ts_code=c, start_date=start, end_date=end)
            except Exception: continue
            if d is None or not len(d): continue
            d["trade_date"]=d["trade_date"].astype(str); d=d.sort_values("trade_date")
            vol_map[c]=pd.Series(d["vol"].values, index=pd.to_datetime(d["trade_date"]))
        if not vol_map: out[code]={"name":a["name"],"vol":[],"n_leader":0}; continue
        # 对齐合并求和
        series={}
        for c,v in vol_map.items(): series[c]=v
        m=pd.DataFrame(series).ffill().sum(axis=1)  # 龙头量能合计
        out[code]={"name":a["name"],"dates":[d.strftime("%Y%m%d") for d in m.index],
                   "vol":[round(float(x),2) for x in m]}
        vol=m.values
        v=np.array(vol, dtype=float)
        n=len(v)
        # vol_pct120 需要 >=120 交易日；不足时用可用窗口回退(标注 n), 避免特征缺失
        w=min(120, n)
        if n>=30:
            out[code]["vol_pct120"]=round(float((v[-w:]<=v[-1]).mean()),3) if w>0 else None
            out[code]["vol_z20"]=round(float((v[-1]-v[-20:].mean())/v[-20:].std()),3) if n>=20 and v[-20:].std()>0 else None
            out[code]["ratio"]=round(float(v[-5:].mean()/v[-60:].mean()),3) if n>=60 and v[-60:].mean()>0 else None
            out[code]["n_days"]=int(n)
        out[code]["n_leader"]=len(codes)
        done+=1
    json.dump(out, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", OUT, "concepts_with_vol", done, "of top", TOP_N)
    for c in list(out.keys())[:3]:
        print(c, out[c].get("name"), "vol_pct120=",out[c].get("vol_pct120"),"vol_z20=",out[c].get("vol_z20"),"ratio=",out[c].get("ratio"),"n_days=",out[c].get("n_days"),"n_leader=",out[c].get("n_leader"))
if __name__=="__main__": main()

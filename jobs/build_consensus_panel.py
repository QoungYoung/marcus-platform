# -*- coding: utf-8 -*-
"""PIT 共识痕迹面板：龙虎榜/机构席位/涨停/资金流/两融/北向/估值。

只做“可观察的共识痕迹”，不推断真实想法。
"""
from __future__ import annotations
import json
import argparse, os, sys
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query
OUT=os.path.join(ROOT,"data/consensus"); RAW=os.path.join(OUT,"raw")
os.makedirs(RAW,exist_ok=True)

def get(api,date):
    p=os.path.join(RAW,api,f"{date}.parquet")
    if os.path.exists(p): return pd.read_parquet(p)
    os.makedirs(os.path.dirname(p),exist_ok=True)
    try:
        df=relay_query(api,trade_date=date)
    except Exception as e:
        print("skip",api,date,str(e)[:100]); return pd.DataFrame()
    if df is not None and len(df): df.to_parquet(p,index=False)
    return df if df is not None else pd.DataFrame()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--start",default="20250301"); ap.add_argument("--end",default="20250331")
    a=ap.parse_args()
    cal=relay_query("trade_cal",exchange="SSE",start_date=a.start,end_date=a.end,is_open="1")
    dates=sorted(cal.cal_date.astype(str).tolist())
    rows=[]
    for d in dates:
        db=get("daily_basic",d); tl=get("top_list",d); ti=get("top_inst",d)
        li=get("limit_list_d",d); mf=get("moneyflow",d); mg=get("margin_detail",d); hs=get("hsgt_top10",d)
        if db is None or not len(db): continue
        base=db[["ts_code","total_mv","circ_mv","pe_ttm","pb","turnover_rate"]].copy()
        # 龙虎榜
        if len(tl):
            x=tl.groupby("ts_code").agg(lhb_net=("net_amount","sum"),lhb_count=("net_amount","size")).reset_index()
            base=base.merge(x,on="ts_code",how="left")
        # 机构/游资席位
        if len(ti):
            ti=ti.copy(); ex=ti.exalter.fillna("")
            ti["is_inst"]=ex.str.contains("机构专用|沪股通|深股通",regex=True)
            x=ti.groupby("ts_code").agg(inst_net=("net_buy",lambda s: float(s[ti.loc[s.index,"is_inst"]].sum())),
                                        hot_net=("net_buy",lambda s: float(s[~ti.loc[s.index,"is_inst"]].sum()))).reset_index()
            base=base.merge(x,on="ts_code",how="left")
        # 涨停
        if len(li):
            up=li[li.limit=="U"]
            if len(up):
                x=up.groupby("ts_code").agg(limit_times=("limit_times","max"),limit_up=("limit","size"),
                                             industry=("industry","first"),name=("name","first")).reset_index()
                base=base.merge(x,on="ts_code",how="left")
        # 资金流（大单+超大单净额）
        if len(mf):
            mf=mf.copy()
            mf["large_net"]=(mf.buy_lg_amount.fillna(0)+mf.buy_elg_amount.fillna(0)
                             -mf.sell_lg_amount.fillna(0)-mf.sell_elg_amount.fillna(0))
            x=mf.groupby("ts_code").agg(large_net=("large_net","sum"),net_mf=("net_mf_amount","sum")).reset_index()
            base=base.merge(x,on="ts_code",how="left")
        # 两融
        if len(mg):
            x=mg.groupby("ts_code").agg(rzye=("rzye","sum"),rzrqye=("rzrqye","sum")).reset_index()
            base=base.merge(x,on="ts_code",how="left")
        # 北向 top10 只能拿 amount，net_amount 为空
        if len(hs):
            x=hs.groupby("ts_code").agg(north_amount=("amount","sum")).reset_index()
            base=base.merge(x,on="ts_code",how="left")
        base["date"]=d
        rows.append(base)
    df=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()
    if not len(df): print("empty"); return
    df.to_parquet(os.path.join(OUT,"panel.parquet"),index=False)
    # 当日横截面排名（不是共识总分，只给各维度）
    for c in ["lhb_net","inst_net","hot_net","large_net","net_mf","north_amount","total_mv","turnover_rate"]:
        if c in df: df[c+"_rank"]=df.groupby("date")[c].rank(ascending=False,method="min")
    df.to_parquet(os.path.join(OUT,"panel_ranked.parquet"),index=False)
    print(json.dumps({"dates":len(dates),"rows":len(df),"cols":df.columns.tolist()},ensure_ascii=False,indent=1))
    # 示例：3/3 各维度 top
    d0=dates[0]
    for c in ["lhb_net","inst_net","hot_net","large_net","north_amount"]:
        if c in df:
            z=df[(df.date==d0)].nlargest(5,c)[["ts_code","name",c]] if "name" in df else df[df.date==d0].nlargest(5,c)[["ts_code",c]]
            print(d0,c); print(z.to_string(index=False))

if __name__=="__main__": main()

# -*- coding: utf-8 -*-
"""
build_concept_matrix.py — 用 tushare 抓东财概念数据并缓存。
输出: data/concept_hist.json = { ts_code: {name, dates[], close[], net_amount[], leader[]} }
运行于 worker 容器（需 tushare 网络）。本地 data 迁移后从 tushare 缓存。
"""
import os, sys, json, time
sys.path.insert(0,"/app/core")
import pandas as pd
import tushare as ts
from _api_config import get_tushare_pro
pro=get_tushare_pro()

OUT="/app/data/concept_hist.json"
INDEX_CSV="/app/data/指数数据/index_daily/000001.SH.csv"
N_DAYS=int(os.getenv("CONCEPT_N_DAYS","250"))
SPECIAL={"HS300_","融资融券","AH股","昨日涨停","昨日连板","昨日触板","涨停分析","预盈预增","预亏预减","ST股","次新股"}

def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true", help="增量模式: 只抓现有缓存之后的新交易日并追加")
    ap.add_argument("--date", default=None, help="抓取截止日期 YYYYMMDD (默认指数最新)")
    args=ap.parse_args()
    idx=pd.read_csv(INDEX_CSV, parse_dates=["trade_date"]).sort_values("trade_date")
    dates=[d.strftime("%Y%m%d") for d in idx["trade_date"].tail(N_DAYS)]
    if args.date:
        dates=[d for d in dates if d<=args.date]
    # 增量: 读现有缓存, 只抓新日期
    prev={}
    if args.incremental and os.path.exists(OUT):
        try: prev=json.load(open(OUT,encoding="utf-8"))
        except Exception: prev={}
        if prev:
            have=max(max(a["dates"]) for a in prev.values() if a["dates"])
            dates=[d for d in dates if d>have]
            if not dates:
                print("INCREMENTAL: 无新交易日 (已有到", have, ")", file=sys.stderr); return
            print("INCREMENTAL: 已有到", have, ", 追加", len(dates), "天", dates[0], "->", dates[-1], file=sys.stderr)
    print("fetching", len(dates), "trade dates,", dates[0] if dates else "none", "->", dates[-1] if dates else "", file=sys.stderr)
    # 每概念: ts_code -> {name, dates, close, net_amount, leader}
    agg={c: {"name":a["name"],"dates":list(a["dates"]),"close":list(a["close"]),"net_amount":list(a["net_amount"]),"leader":list(a["leader"])} for c,a in prev.items()}
    for i,dt in enumerate(dates):
        try:
            df=pro.moneyflow_ind_dc(trade_date=dt, content_type="概念")
        except Exception as e:
            print("ERR", dt, str(e)[:80], file=sys.stderr); continue
        if df is None or not len(df): continue
        for _,r in df.iterrows():
            code=r["ts_code"]; nm=r["name"]
            if nm in SPECIAL or nm.startswith("HS300"): continue
            a=agg.setdefault(code, {"name":nm,"dates":[],"close":[],"net_amount":[],"leader":[]})
            a["dates"].append(dt); a["close"].append(float(r["close"]))
            a["net_amount"].append(float(r["net_amount"]) if pd.notna(r["net_amount"]) else 0.0)
            a["leader"].append(str(r["buy_sm_amount_stock"]) if pd.notna(r["buy_sm_amount_stock"]) else "")
        if (i+1)%25==0: print("...", i+1, file=sys.stderr)
    # 前向填充缺失日期, 记录覆盖天数
    out={}
    for code,a in agg.items():
        s=pd.Series(a["close"], index=pd.to_datetime(a["dates"])).sort_index()
        full=pd.Series(index=pd.to_datetime(dates), dtype=float)
        full=full.reindex(sorted(set(pd.to_datetime(dates)) | set(s.index)))
        close=full.reindex(full.index).values
        # forward fill close, net_amount
        s_close=pd.Series(a["close"], index=pd.to_datetime(a["dates"])).reindex(full.index)
        s_net=pd.Series(a["net_amount"], index=pd.to_datetime(a["dates"])).reindex(full.index)
        s_ldr=pd.Series(a["leader"], index=pd.to_datetime(a["dates"])).reindex(full.index)
        s_close=s_close.ffill(); s_net=s_net.ffill().fillna(0.0); s_ldr=s_ldr.ffill().fillna("")
        out[code]={"name":a["name"],"dates":[d.strftime("%Y%m%d") for d in full.index],
                   "close":[round(float(x),2) if pd.notna(x) else None for x in s_close],
                   "net_amount":[round(float(x),1) for x in s_net],
                   "leader":[str(x) for x in s_ldr],
                   "coverage":int(s_close.notna().sum())}
    json.dump(out, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", OUT, "concepts", len(out), file=sys.stderr)
    # 打印几个样例
    sample=list(out.keys())[:4]
    for c in sample:
        print(c, out[c]["name"], "cov", out[c]["coverage"], "last_close", out[c]["close"][-1],"last_net", out[c]["net_amount"][-1])
if __name__=="__main__": main()

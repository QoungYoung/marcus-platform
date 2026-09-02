# -*- coding: utf-8 -*-
import pandas as pd, numpy as np, json
CSV='data/指数数据/index_daily/000001.SH.csv'; OUT='data/wave_pivots.json'
df=pd.read_csv(CSV, parse_dates=['trade_date']).sort_values('trade_date')
c=df['close'].values; dt=df['trade_date'].values; N=len(c); w=120
piv=[]
for i in range(N):
    lo=max(0,i-w); hi=min(N,i+w+1)
    if c[i]==c[lo:hi].max(): piv.append((i,float(c[i]),'H'))
    if c[i]==c[lo:hi].min(): piv.append((i,float(c[i]),'L'))
piv.sort(key=lambda x:x[0])
ded=[]
for i,v,t in piv:
    if ded and ded[-1][2]==t and (i-ded[-1][0])<=10:
        if (t=='H' and v>=ded[-1][1]) or (t=='L' and v<=ded[-1][1]): ded[-1]=[i,v,t]
    else: ded.append([i,v,t])
res=[{"date":str(pd.Timestamp(dt[i]).date()),"value":round(v,1),"type":t} for i,v,t in ded]
json.dump({"source":str(pd.Timestamp(df['trade_date'].min()).date())+"~"+str(pd.Timestamp(df['trade_date'].max()).date()),"window":w,"pivots":res}, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=0)
print("n=",len(res))
# print pivots from 2014 onward (recent relevant)
for p in res:
    if p["date"]>="2014-01-01": print(f"  {p['date']} {p['type']} {p['value']}")

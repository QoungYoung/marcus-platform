# -*- coding: utf-8 -*-
"""B反候选多因子 v2：K线形态+均线+量能+动量+浪型+相对强弱。"""
from __future__ import annotations
import os,sys,json
import numpy as np,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query
OUT=os.path.join(ROOT,'data/core_pit')

def load_daily():
 d=relay_query('fund_daily',ts_code='588170.SH',start_date='20250101',end_date='20261021').sort_values('trade_date').reset_index(drop=True)
 for c in ['open','high','low','close','vol','amount']: d[c]=pd.to_numeric(d[c],errors='coerce')
 return d

def wave_map():
 m={}
 p=os.path.join(ROOT,'data/wave_state_history')
 if os.path.isdir(p):
  for f in os.listdir(p):
   if not f.endswith('.json'): continue
   try:
    x=json.load(open(os.path.join(p,f),encoding='utf-8'))
    m[str(x.get('date') or f[:8])]=x
   except Exception: pass
 return m

def nearest_wave(m,d):
 ks=sorted(k for k in m if k<=d)
 return m[ks[-1]] if ks else {}

def main():
 d=load_daily(); wm=wave_map()
 d['ma5']=d.close.rolling(5).mean(); d['ma10']=d.close.rolling(10).mean(); d['ma20']=d.close.rolling(20).mean(); d['ma60']=d.close.rolling(60).mean()
 d['hi20']=d.high.rolling(20).max(); d['hi60']=d.high.rolling(60).max(); d['hi120']=d.high.rolling(120).max()
 d['lo20']=d.low.rolling(20).min(); d['lo60']=d.low.rolling(60).min()
 d['a5']=d.amount.rolling(5).mean(); d['a20']=d.amount.rolling(20).mean(); d['v5']=d.vol.rolling(5).mean(); d['v20']=d.vol.rolling(20).mean()
 d['r1']=d.close.pct_change(); d['r5']=d.close.pct_change(5); d['r10']=d.close.pct_change(10); d['r20']=d.close.pct_change(20); d['r60']=d.close.pct_change(60)
 # RSI14
 delta=d.close.diff(); up=delta.clip(lower=0).rolling(14).mean(); dn=(-delta.clip(upper=0)).rolling(14).mean(); d['rsi14']=100-100/(1+up/dn.replace(0,np.nan))
 # MACD
 ema12=d.close.ewm(span=12).mean(); ema26=d.close.ewm(span=26).mean(); d['dif']=ema12-ema26; d['dea']=d.dif.ewm(span=9).mean(); d['macd']=(d.dif-d['dea'])*2
 # ATR
 tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1); d['atr14']=tr.rolling(14).mean()/d.close
 # Bollinger
 mid=d.close.rolling(20).mean(); sd=d.close.rolling(20).std(); d['bb_pos']=(d.close-(mid-2*sd))/(4*sd)
 # candle
 d['body']=(d.close-d.open)/d.close; d['range']=(d.high-d.low)/d.close
 d['upper']=(d.high-d[['open','close']].max(axis=1))/d.close; d['lower']=(d[['open','close']].min(axis=1)-d.low)/d.close
 d['lower_ratio']=d.lower/(d.range.replace(0,np.nan)); d['upper_ratio']=d.upper/(d.range.replace(0,np.nan)); d['close_pos']=(d.close-d.low)/(d.high-d.low)
 d['hammer']=((d.lower>=2*d.body.abs())&(d.upper<=d.body.abs())&(d.close_pos>=0.6)).astype(int)
 d['doji']=((d.body.abs()<=0.1*d.range)&(d.range>0)).astype(int)
 d['bull_engulf']=((d.close>d.open)&(d.close.shift()<d.open.shift())&(d.close>=d.open.shift())&(d.open<=d.close.shift())).astype(int)
 d['piercing']=((d.close>d.open)&(d.close.shift()<d.open.shift())&(d.open<d.close.shift())&(d.close>(d.open.shift()+d.close.shift())/2)).astype(int)
 d['morning_star']=((d.close.shift(2)<d.open.shift(2))&(d.body.shift(1).abs()<0.01)&(d.close>d.open)&(d.close>(d.open.shift(2)+d.close.shift(2))/2)).astype(int)
 d['long_lower']=(d.lower_ratio>=0.5).astype(int)
 # next-day break hammer high (not lookahead; only historical feature)
 d['next_break_hammer']=0
 for i in range(1,len(d)-1):
  if d.hammer.iloc[i] and d.high.iloc[i+1]>d.high.iloc[i]: d.loc[i+1,'next_break_hammer']=1
 cand=pd.read_csv(os.path.join(OUT,'b_rebound_minute_candidates.csv'),dtype={'date':str})
 rows=[]
 for _,r in cand.iterrows():
  z=d[d.trade_date==r.date]
  if not len(z): continue
  i=z.index[0]; x=d.iloc[i]; w=nearest_wave(wm,r.date)
  rows.append({**r.to_dict(),
   'open':x.open,'high':x.high,'low':x.low,'close':x.close,
   'body':round(x.body,4),'upper_ratio':round(x.upper_ratio,3),'lower_ratio':round(x.lower_ratio,3),'close_pos_d':round(x.close_pos,3),
   'hammer':int(x.hammer),'doji':int(x.doji),'bull_engulf':int(x.bull_engulf),'piercing':int(x.piercing),'morning_star':int(x.morning_star),'long_lower':int(x.long_lower),
   'r1_d':round(x.r1*100,2),'r5_d':round(x.r5*100,2),'r10_d':round(x.r10*100,2),'r20_d':round(x.r20*100,2),'r60_d':round(x.r60*100,2),
   'ma5_d':round(x.close/x.ma5-1,4),'ma10_d':round(x.close/x.ma10-1,4),'ma20_d':round(x.close/x.ma20-1,4),'ma60_d':round(x.close/x.ma60-1,4),
   'dd20':round(x.close/x.hi20-1,4),'dd60':round(x.close/x.hi60-1,4),'dd120':round(x.close/x.hi120-1,4),
   'dist_lo20':round(x.close/x.lo20-1,4),'dist_lo60':round(x.close/x.lo60-1,4),
   'amt5_20':round(x.a5/x.a20,3) if x.a20 else None,'vol5_20':round(x.v5/x.v20,3) if x.v20 else None,
   'rsi14':round(x.rsi14,2) if pd.notna(x.rsi14) else None,'macd':round(x.macd,4) if pd.notna(x.macd) else None,'atr14':round(x.atr14,4) if pd.notna(x.atr14) else None,'bb_pos':round(x.bb_pos,2) if pd.notna(x.bb_pos) else None,
   'wave_level':w.get('level'),'wave_sub':w.get('sub_level'),'wave_op':w.get('operation'),'wave_date':w.get('date')})
 df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,'b_rebound_features_v2.csv'),index=False,encoding='utf-8-sig')
 print(df[['date','label','body','lower_ratio','close_pos_d','hammer','bull_engulf','long_lower','r20_d','ma20_d','dd60','amt5_20','rsi14','macd','wave_sub']].to_string(index=False))
 print('n',len(df),'cols',len(df.columns))
if __name__=='__main__': main()

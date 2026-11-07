#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ab_equity.py — 由 ab_local_*.json 生成 10/23–11/04 组合净值（首日空仓）。"""
from __future__ import annotations
import json,os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATES=['20261023','20261026','20261027','20261028','20261029','20261030','20261102','20261103','20261104']
def main():
 eq=1.0; peak=1.0; rows=[]; wins=0; n=0; first=True
 for d in DATES:
  p=os.path.join(ROOT,'data','backtest',f'ab_local_{d}.json')
  if not os.path.exists(p):
   rows.append({'date':d,'status':'missing'}); continue
  x=json.load(open(p)); t=x.get('trade') or {}; ret=(t.get('ret') or {}).get('ret')
  sym=t.get('symbol'); action=t.get('action')
  if first:
   first=False
   rows.append({'date':d,'action':'cash_first_day','symbol':None,'ret_pct':0.0,'equity':eq})
   continue
  if action in ('stay_source','B') and sym and ret is not None:
   r=float(ret)/100; eq*=1+r; n+=1; wins+=1 if r>0 else 0
   peak=max(peak,eq); dd=(eq/peak-1)*100
   rows.append({'date':d,'action':action,'target':t.get('target'),'symbol':sym,
                'ret_pct':round(float(ret),2),'equity':round(eq,6),
                'cum_pct':round((eq-1)*100,2),'drawdown_pct':round(dd,2)})
  else:
   peak=max(peak,eq); dd=(eq/peak-1)*100
   rows.append({'date':d,'action':'cash','target':t.get('target'),'symbol':sym,
                'ret_pct':0.0,'equity':round(eq,6),'cum_pct':round((eq-1)*100,2),
                'drawdown_pct':round(dd,2),'reason':t.get('error') or 'no_trade'})
 out={'start':'20261023','end':'20261104','first_day':'cash','final_equity':round(eq,6),
      'total_return_pct':round((eq-1)*100,2),'trades':n,'wins':wins,
      'win_rate_pct':round(wins/n*100,2) if n else None,'rows':rows}
 os.makedirs(os.path.join(ROOT,'data','backtest'),exist_ok=True)
 json.dump(out,open(os.path.join(ROOT,'data','backtest','ab_equity.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
 print(json.dumps(out,ensure_ascii=False,indent=1))
if __name__=='__main__': main()

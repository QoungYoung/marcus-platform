#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顺序跑 A/B，每步把当前持仓传给 agent。"""
import json,os,subprocess,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'core'))
DATES=['20261023','20261026','20261027','20261028','20261029','20261030','20261102','20261103','20261104']
PY=os.path.join(ROOT,'.venv/bin/python')
from tushare_relay import relay_query
sys.path.insert(0,os.path.join(ROOT,'scripts'))
from backtest_ab_local import source_symbol
def px(sym,date):
    x=relay_query('daily',ts_code=sym,trade_date=date,limit=5)
    if x is None or len(x)==0: return None,None
    return float(x.iloc[0]['open']),float(x.iloc[0]['close'])
pos=None; cash=100000.0; ledger=[]
for d in DATES:
    hold='空仓' if not pos else f"{pos['symbol']} 成本{pos['entry']:.2f}"
    env=dict(os.environ); env['WOLF_V3_AGENT_URL']='http://127.0.0.1:3001/chat'
    r=subprocess.run([PY,os.path.join(ROOT,'scripts/backtest_ab_local.py'),'--date',d,'--holding',hold],env=env,capture_output=True,text=True,timeout=900)
    p=os.path.join(ROOT,'data/backtest',f'ab_local_{d}.json')
    if not os.path.exists(p):
        ledger.append({'date':d,'error':r.stderr[-300:]}); continue
    o=json.load(open(p)); tr=o.get('trade') or {}; A=o.get('A') or {}
    nxt=(tr.get('ret') or {}).get('next_date') if isinstance(tr.get('ret'),dict) else None
    # A 不切 + 无仓：后续日期买当前题材 leader，第一天空仓
    if (not A.get('need_switch')) and pos is None and nxt and d != DATES[0]:
        _ss=source_symbol(d)
        _bo,_bc=px(_ss,nxt)
        if _bo: pos={'symbol':_ss,'entry':_bo,'date':nxt}
    if A.get('need_switch') and tr.get('symbol'):
        # 换仓：旧仓按 next open 卖，新仓 next open 买
        if pos and nxt:
            _so,_sc=px(pos['symbol'],nxt)
            if _so: cash*= _so/pos['entry']
        if nxt:
            _bo,_bc=px(tr['symbol'],nxt)
            if _bo: pos={'symbol':tr['symbol'],'entry':_bo,'date':nxt}
    eq=cash
    if pos:
        _o,_c=px(pos['symbol'],nxt or d)
        if _c: eq=cash*(_c/pos['entry'])
    ledger.append({'date':d,'A_switch':A.get('need_switch'),'trade':tr.get('action'),'symbol':tr.get('symbol'),
                   'holding':pos['symbol'] if pos else None,'equity':round(eq,2)})
    print(d,ledger[-1],flush=True)
json.dump({'ledger':ledger,'final':ledger[-1].get('equity') if ledger else cash},open(os.path.join(ROOT,'data/backtest/ab_portfolio.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('FINAL',ledger[-1].get('equity') if ledger else cash)

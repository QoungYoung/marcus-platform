#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_ab_apply.py — 把 theme_ab_signals 转成 theme_sleeve_selected。"""
from __future__ import annotations
import argparse,json,os,re,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'backend'))
ALIAS={'商业航天':'space','卫星互联网':'satellite','AI应用':'ai_app','医疗器械':'medical_device'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--trade',action='store_true'); a=ap.parse_args()
    import psycopg2
    c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor()
    cur.execute("""select target,leader,wind,mid,mode from theme_ab_signals
                   where trade_date=%s and status in ('proposed','approved') order by slot desc limit 1""",(a.date,))
    row=cur.fetchone()
    if not row:
        print('no signal'); return 0
    target,leader,wind,mid,mode=row
    if mode=='C_exit' and leader:
        import subprocess
        subprocess.run([sys.executable,os.path.join(ROOT,'jobs/theme_ab_exit.py'),'--symbol',leader,'--date',a.date],check=False)
        cur.execute("update theme_ab_signals set status='approved' where trade_date=%s and status='proposed'",(a.date,));c.commit();c.close()
        print('exit applied',leader);return 0
    if not (leader and re.match(r'^\d{6}\.(SH|SZ|BJ)$',leader)):
        cur.execute("update theme_ab_signals set status='rejected' where trade_date=%s and status in ('proposed','approved')",(a.date,)); c.commit()
        print('reject: leader not code'); return 0
    tid=ALIAS.get(target,target)
    sel={'as_of':a.date.replace('-',''),'sleeve':'theme','candidates':1,
         'selected':[{'date':a.date.replace('-',''),'sleeve':'theme','theme_id':tid,'theme_name':target,
                      'symbol':leader,'role':'leader','tier':'ab','first_linkage':True,
                      'entry_type':'first_day_probe','size_multiplier':0.5,
                      'agent_action':'buy'}]}
    p=os.path.join(ROOT,'data','mainline',a.date.replace('-',''),'theme_sleeve_selected.json')
    os.makedirs(os.path.dirname(p),exist_ok=True); json.dump(sel,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    try:
        from theme_sleeve_store import save
        save(a.date.replace('-',''),'selected',sel)
    except Exception as e: print('PG save fail',e)
    cur.execute("update theme_ab_signals set status='approved' where trade_date=%s and status in ('proposed','approved')",(a.date,)); c.commit(); c.close()
    print('applied',target,leader,tid)
    if a.trade:
        import subprocess
        subprocess.run([sys.executable,os.path.join(ROOT,'jobs/theme_sleeve_trade.py'),'--execute'],check=False)
if __name__=='__main__': main()

# -*- coding: utf-8 -*-
"""generate_today_plan.py — 生成今日(服务器当前日期)计划: 结构优先主线主题计划 + 指数回补计划"""
import os, sys, json, datetime
sys.path.insert(0,'/app/apps/main_line')
sys.path.insert(0,'/app')
import plan_compiler_v0 as pc
from app.services.plan_runner import upsert_plan
D=os.environ.get('DATA_DIR','/app/data')
def daily(code):
    out={}
    for root in ['stock_5m_bt','recent_sync']:
        p=os.path.join(D,root,code+'.json')
        try: d=json.load(open(p,encoding='utf-8'))
        except: d={}
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k,{'close':float(bs[-1]['close']),'vol':sum(float(b.get('vol') or 0) for b in bs)})
    return out
def pos_pct(dc,date,win=90):
    ds=sorted([k for k in dc if k<=date and dc[k]['close']])
    if len(ds)<20: return None
    vals=[dc[k]['close'] for k in ds[-win:]]; lo=min(vals); hi=max(vals); cur=dc[ds[-1]]['close']
    return round((cur-lo)/(hi-lo)*100,1) if hi>lo else 50.0
def vol_ratio(dc,date,back=5):
    ds=sorted([k for k in dc if k<=date and dc[k]['vol']])
    if len(ds)<back+1: return None
    cur=dc[ds[-1]]['vol']; prev=[dc[k]['vol'] for k in ds[-1-back:-1]]
    avg=sum(prev)/len(prev) if prev else 0
    return round(cur/avg,2) if avg>0 else None
def stable(dc,date):
    ds=sorted([k for k in dc if k<=date and dc[k]['close']])
    if len(ds)<2: return False
    return dc[ds[-1]]['close']>=dc[ds[-2]]['close']
today=datetime.datetime.now().strftime('%Y-%m-%d')
print('今日=',today,flush=True)
CANDS=[('液冷','BK1138.DC',1.0,'301018'),('算力/国算','BK1134.DC',1.0,'601138'),('数据中心','BK0922.DC',1.0,'000977'),
       ('半导体','BK0917.DC',1.0,'002371'),('CPO','BK1128.DC',1.0,'300308'),('国产芯片','BK0891.DC',1.0,'688981'),
       ('人工智能','BK0800.DC',1.0,'000977'),('军工','BK0490.DC',0.5,'601138'),('化工原料','BK0512.DC',0.5,'002165'),('黄金','BK0547.DC',0.4,'002945')]
scores=[]
for name,code,ml,proxy in CANDS:
    s=pc.theme_pit_score(code,today,mainline=ml)
    if s: scores.append((s['thesis_score'],name,code,ml,proxy,s))
scores.sort(key=lambda x:x[0],reverse=True)
print('\n=== 今日主题结构优先排序 (2026-09-03) ===',flush=True)
for sc,name,code,ml,proxy,s in scores:
    print('  %-12s %.2f | rel=%.2f 主线=%.2f 资金=%.2f' % (name,sc,s['rel'],s['mainline'],s['fund']),flush=True)
theme_plans=[]
for sc,name,code,ml,proxy,s in scores:
    if sc<0.70: break
    dc=daily(proxy); pp=pos_pct(dc,today); vr=vol_ratio(dc,today); st=stable(dc,today)
    plan={'id':'plan_theme_%s_%s' % (name,today.replace('-','')),'type':'theme_plan','subject':name,'horizon':'半年',
          'created_at':today,'status':'armed',
          'thesis':'%s (结构优先主题分=%.2f; rel=%.2f/主线=%.2f)' % (name,sc,s['rel'],s['mainline']),
          'trigger':{'symbol':proxy,'pos_pct_max':30.0,'vol_min':0.8,'require_stable':True},
          'action':'build_on_dip(低位+企稳放量分批建仓)','gate':'crowding+P2Gate 放行',
          'pit_score':s,'today_context':{'pos_pct':pp,'vol_ratio':vr,'stable':st}}
    theme_plans.append(plan)
lv=pc.key_levels(today); rp=None
if lv:
    cur=lv['close']; below=[(k,v) for k,v in [('ma20',lv['ma20']),('r382',lv['r382']),('box30_low',lv['box30_low'])] if v and v<cur]
    if below:
        k,v=below[0]
        rp={'id':'plan_refill_%s' % today.replace('-',''),'type':'refill_plan','subject':'上证指数','horizon':'2周',
            'created_at':today,'status':'armed','thesis':'大4浪反弹中, 预置关键位回补T仓',
            'trigger':{'key_level':k,'value':v,'bands':[('ma20',lv['ma20']),('r382',lv['r382']),('box30_low',lv['box30_low'])]},
            'action':'refill_T(跌破/接近关键位企稳→回补上周T仓, 目标差价3-5点)','gate':'P2Gate(wave+macro) 放行才回补'}
library=theme_plans+([rp] if rp else [])
# Upsert 到 PostgreSQL(plan_library 表)
for _p in library:
    try: upsert_plan(_p)
    except Exception as _e: print('UPSERT_ERR', _p.get('id'), _e, flush=True)
json.dump({'generated':'plan_compiler_today','generated_at':datetime.datetime.now().isoformat(),'library':library},
          open(os.path.join(D,'plan_library.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('\n=== 今日计划 (plan_library.json) ===',flush=True)
for p in library:
    print('  [%s] %s | %s | 触发=%s | 动作=%s' % (p['type'],p['subject'],p.get('thesis','')[:60],json.dumps(p.get('trigger',{}),ensure_ascii=False),p.get('action')),flush=True)
    if p.get('today_context'): print('       今日: pos_pct=%s vol_ratio=%s stable=%s' % (p['today_context']['pos_pct'],p['today_context']['vol_ratio'],p['today_context']['stable']),flush=True)
print('\nWROTE plan_library.json (今日计划, 主题%d + 回补%d)' % (len(theme_plans), 1 if rp else 0),flush=True)

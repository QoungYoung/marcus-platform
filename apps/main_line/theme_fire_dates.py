# -*- coding: utf-8 -*-
"""theme_fire_dates.py — 各主题(0.80并列组+芯片)首次触发日期 PIT 回放 08-20~09-03"""
import os, json
D='/app/data'
def daily(code):
    out={}
    for root in ['stock_5m_bt','recent_sync']:
        p=os.path.join(D,root,code+'.json')
        try: d=json.load(open(p,encoding='utf-8'))
        except: d={}
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs:
                out.setdefault(k,{'close':float(bs[-1]['close']),'low':min(float(b['low']) for b in bs),
                                  'high':max(float(b['high']) for b in bs),'vol':sum(float(b.get('vol') or 0) for b in bs)})
    return out
def pos_pct(dc, date, win=90):
    ds=sorted([k for k in dc if k<=date and dc[k]['close']])
    if len(ds)<20: return None
    vals=[dc[k]['close'] for k in ds[-win:]]
    lo=min(vals); hi=max(vals); cur=dc[ds[-1]]['close']
    return round((cur-lo)/(hi-lo)*100,1) if hi>lo else 50.0
def vol_ratio(dc, date, back=5):
    ds=sorted([k for k in dc if k<=date and dc[k]['vol']])
    if len(ds)<back+1: return None
    cur=dc[ds[-1]]['vol']; prev=[dc[k]['vol'] for k in ds[-1-back:-1]]
    avg=sum(prev)/len(prev) if prev else 0
    return round(cur/avg,2) if avg>0 else None
def stable(dc, date):
    ds=sorted([k for k in dc if k<=date and dc[k]['close']])
    if len(ds)<2: return False
    return dc[ds[-1]]['close'] >= dc[ds[-2]]['close']
THEMES=[
 ('算力/国算','601138'),('液冷','301018'),('数据中心','000977'),('半导体','002371'),('CPO','300308'),('国产芯片','688981'),
]
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
print('%-12s %-8s %-24s %-14s' % ('主题','代理','首次触发(位置<30%+量比≥0.8+企稳)','次日/后续触发'))
for name,code in THEMES:
    dc=daily(code)
    fired=[]
    for d in DAYS:
        pp=pos_pct(dc,d); vr=vol_ratio(dc,d); st=stable(dc,d)
        if pp is not None and pp<30 and vr is not None and vr>=0.8 and st:
            fired.append((d,pp,vr))
    first=fired[0] if fired else None
    extra=[(d,pp,vr) for d,pp,vr in fired[1:3]]
    print('%-12s %-8s %-24s %-14s' % (name, code,
        ('%s(pp%.0f%%/vr%.1f)' % (first[0], first[1], first[2])) if first else '无触发',
        ('; '.join('%s(%.0f%%)' % (d,pp) for d,pp,vr in extra)) if extra else '-'))
    if fired:
        print('      all fire dates:', [f[0] for f in fired], flush=True)

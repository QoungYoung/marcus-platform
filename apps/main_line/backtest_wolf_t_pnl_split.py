# -*- coding: utf-8 -*-
import os, sys, json, statistics
D='/app/data'; sys.path.insert(0,'/app')
from app.services.wolf_t_rules import zheng_t_buy, t_cycle_pnl
def daily(code):
    out={}
    for root in ['recent_sync','stock_5m_bt']:
        p=os.path.join(D,root,code+'.json')
        try: d=json.load(open(p,encoding='utf-8'))
        except: continue
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k,bs)
    return out
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
conf=[]; days=[]
for c in ['159516','588170','301018']:
    dc=daily(c)
    for d in DAYS:
        bs=dc.get(d)
        if not bs: continue
        prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
        if not prev or not zheng_t_buy(bs,prev)[0]: continue
        cyc=t_cycle_pnl(bs,2.5)
        if not cyc: continue
        buy=cyc['buy']
        if cyc.get('sell'):
            pnl=(cyc['sell']/buy-1)*100; conf.append(pnl)
        else:
            sell=float(bs[-1]['close']); pnl=(sell/buy-1)*100; days.append(pnl)
def stat(name, arr):
    if not arr: return None
    return {'n':len(arr),'avg':round(sum(arr)/len(arr),2),'win_rate':round(sum(1 for x in arr if x>0)/len(arr)*100),'total_sum':round(sum(arr),2)}
print('确认制T出: ', stat('conf',conf))
print('day_end(未确认→收盘): ', stat('day',days))
print()
conf_all=stat('conf',conf); days_all=stat('day',days)
all_arr=conf+days
print('合并(20笔): ', stat('all',all_arr))
print()
print('若只做确认制(砍掉day_end噪音):')
if conf_all:
    print('  仅确认单: n=%d 平均=%+.2f%% 胜率=%.0f%% 收益和=%+.2f%%' % (conf_all['n'],conf_all['avg'],conf_all['win_rate'],conf_all['total_sum']))
print('  数字对比: 平均从%+.2f%%(含day_end) 提升到 %+.2f%%，胜率 %.0f%%→%.0f%%' % ((sum(all_arr)/len(all_arr)), conf_all['avg'] if conf_all else 0, (sum(1 for x in all_arr if x>0)/len(all_arr)*100), conf_all['win_rate'] if conf_all else 0))

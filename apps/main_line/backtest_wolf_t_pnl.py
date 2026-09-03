# -*- coding: utf-8 -*-
import os, sys, json
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
WOLF={'20260820':'wait','20260821':'sell(减T半)','20260824':'buy(回补)','20260825':'buy(液冷)','20260826':'wait','20260827':'sell(减全部正T)','20260828':'wait','20260831':'wait','20260901':'buy+尾盘保本','20260902':'buy+14:03 T出2%','20260903':'no_semi_T'}
print('近两周 我们做T每笔收益(正T低吸→确认T出/day_end/防御)  vs 狼大动作')
trades=[]; wolflist=[]
for c in ['159516','588170','301018']:
    dc=daily(c)
    for d in DAYS:
        bs=dc.get(d)
        if not bs: continue
        prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
        if not prev: continue
        if not zheng_t_buy(bs,prev)[0]: continue
        cyc=t_cycle_pnl(bs,2.5)
        if not cyc: continue
        buy=cyc['buy']
        if cyc.get('sell'):
            sell=cyc['sell']; kind='确认T出'
        else:
            sell=float(bs[-1]['close']); kind='day_end(收盘)'
        pnl=(sell/buy-1)*100
        trades.append((d,c,buy,sell,pnl,kind))
        print('  %s %-6s 买@%.3f → %s@%.3f  %+.2f%%  (%s)' % (d,c,buy,kind,sell,pnl,kind[0:6]))
    wolflist.append(c)
# 汇总
if trades:
    pnl=[t[4] for t in trades]
    print()
    print('我们做T: %d 笔 | 平均每笔 %+.2f%% | 累积(简单加总) %+.2f%% | 胜率 %.0f%%' % (len(trades), sum(pnl)/len(pnl), sum(pnl), sum(1 for p in pnl if p>0)/len(pnl)*100))
print()
print('狼大实测(近似): 09-02 T出+2%%(半导体ETF) | 09-01 尾盘保本~0(亏手续费) | 08-27 减全部正T(锁前段利润) | 04-28 减T仓(吃差价)')
print('(对照: 狼大目标吃2-3点≈2-3%%)')

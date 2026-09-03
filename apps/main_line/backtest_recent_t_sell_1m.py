# -*- coding: utf-8 -*-
"""backtest_recent_t_sell_1m.py — 1min 分时T出(排除结构性bar+尾盘) 最近两周 159516/588170"""
import os, json
D='/app/data/recent_sync/1m'
def load(c): return json.load(open(os.path.join(D,c+'.json'),encoding='utf-8'))
def bs(b): return sorted(b,key=lambda x:str(x.get('time') or x.get('trade_time')))
def hm(t):
    try: return int(str(t)[11:13])*100+int(str(t)[14:16]) if len(str(t))>=16 else int(str(t)[8:10])*100+int(str(t)[10:12])
    except: return 0
def in_ok(t): 
    m=hm(t); return 936<=m<=1449   # 排除 09:30/09:31, 11:30-13:00? 13:00<=m 允许(实际13:05后), 及14:50后
def t_sell_1m(bars, look=15):
    b=bs(bars)
    times=[str(x.get('time') or x.get('trade_time')) for x in b]
    closes=[float(x['close']) for x in b]
    vols=[float(x.get('vol') or 0) for x in b]
    n=len(closes)
    if n<look*4+6: return None,''
    for i in range(look, n-look-2):
        if not in_ok(times[i]): continue
        base=sum(vols[i-look:i])/look
        if not (base>0 and vols[i]>base*1.3): continue
        # 从 i 到 14:49 的段内找第一高点
        j=i
        while j<n and in_ok(times[j]): j+=1
        seg=closes[i:j]
        if len(seg)<4: continue
        hi=max(seg); hi_idx=i+seg.index(hi)
        if hi_idx<i+2: continue
        vb=sum(vols[max(i,hi_idx-look):hi_idx])/max(1,(hi_idx-max(i,hi_idx-look)))
        va=sum(vols[hi_idx+1:hi_idx+1+look])/look if hi_idx+look<n else 0
        if not (vb>0 and va<vb*0.8): continue
        after=closes[hi_idx+1:j]
        if not after: continue
        second_hi=max(after)
        second_up = second_hi>hi*0.98 and second_hi<hi*1.005
        if second_up:
            idx2=hi_idx+1+after.index(second_hi)
            return times[idx2], f"峰{hi:.3f} 二次{second_hi:.3f}"
    return None,''
DATES=['2026-08-20','2026-08-21','2026-08-24','2026-08-25','2026-08-26','2026-08-27','2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03']
WOLF_SELL={'2026-08-21':'(14:35 T减半过周末)','2026-08-27':'(11:05 减所有正T留底仓)','2026-09-02':'(14:03 T出2%)'}
print('%-12s %-9s %-12s %-10s %-9s' % ('date','159516','588170','wolf_sell','match?'))
for d in DATES:
    k=d.replace('-','')
    res={}
    for code in ['159516','588170']:
        day=load(code).get(k)
        if not day: res[code]=('','无数据'); continue
        t,desc=t_sell_1m(day)
        res[code]=(t or '-', desc)
    ws=WOLF_SELL.get(d,'')
    m=''
    if ws:
        wtime=ws.split(' ')[0]  # like "14:35"
        m = '✅' if any(res[c][0] and res[c][0][11:16]==wtime for c in res) else '△(时点近似)' if any(res[c][0] for c in res) else '✗'
    print('%-12s %-9s %-12s %-10s %-9s' % (d, res['159516'][0] or '-', res['588170'][0] or '-', ws.replace('(','').replace(')','') if ws else '-', m))
    if ws:
        for c in ['159516','588170']:
            if res[c][0]: print('      ',c,'->',res[c][0],res[c][1],flush=True)
print('DONE',flush=True)

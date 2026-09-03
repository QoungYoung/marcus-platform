# -*- coding: utf-8 -*-
"""replay_wolf_dip_254_5m.py — 生产254/253精确回放(5min) vs 狼大低吸事件
254: 当日5min运行低 <= 前日5min低×1.005 且 盘中量比(同序累计vol/前5日同序均值)<=0.7 且 黄线护栏(bar.close>当日cumVWAP)
253: 上证任意单根5min close跌幅<=-0.4%
买入口径(保守): 信号日收盘; Wolf口径: 事件日收盘; 收益=T+5
"""
import os, sys, json, glob
DATA=os.environ.get('DATA_DIR','data')
def key(s): return str(s).replace('-','')
def load_stock(code6):
    p=os.path.join(DATA,'stock_5m_bt','%s.json'%code6)
    if not os.path.exists(p): return {}
    raw=json.load(open(p,encoding='utf-8'))
    return {str(k): raw[k] for k in raw}
def load_idx():
    raw=json.load(open(os.path.join(DATA,'index_5min_dh.json'),encoding='utf-8'))
    return {str(k).replace('-',''): raw[k] for k in raw}
def day_low(bars): return min(float(b['low']) for b in bars)
def day_close(bars):
    bs=sorted(bars,key=lambda b:str(b.get('time') or b.get('trade_time')))
    return float(bs[-1]['close'])
def ord_low(codes):
    pass
def bars_sorted(bars): return sorted(bars,key=lambda b:str(b.get('time') or b.get('trade_time')))
def cum_vols(bars):
    out=[]; s=0.0
    for b in bars_sorted(bars):
        s+=float(b.get('vol') or 0); out.append(s)
    return out
def cum_amounts(bars):
    out=[]; s=0.0
    for b in bars_sorted(bars):
        s+=float(b.get('amount') or 0); out.append(s)
    return out
def prev_5_avg_cum(daymap, days, di):
    prev=days[max(0,di-5):di]
    rows=[]
    for d in prev:
        bs=bars_sorted(daymap[d])
        if len(bs)>=2:
            rows.append(daymap[d])
    return rows
def m5_dump_day(idx_bars, thresh=0.4):
    bs=bars_sorted(idx_bars)
    prev=None
    for b in bs:
        c=float(b['close'])
        if prev and prev>0 and (c-prev)/prev*100<=-thresh: return True
        prev=c
    return False
def find_254_on(daymap, days, di, thresh_vr=0.7):
    """day index di -> (trigger_time, trigger_close) or None"""
    if di<=0: return None
    prev_d=days[di-1]
    pl=day_low(daymap[prev_d])
    if pl<=0: return None
    cur=daymap[days[di]]
    cbs=bars_sorted(cur)
    if len(cbs)<2: return None
    # baseline 前5日(排除当日)按 bar 序号累计vol均值
    base_days=[days[j] for j in range(max(0,di-5),di)]
    base_cums=[]
    for dd in base_days:
        bb=bars_sorted(daymap[dd])
        base_cums.append(cum_vols(bb))
    cums=cum_vols(cur); amts=cum_amounts(cur)
    rlow=10**18; n=len(cbs)
    for i,b in enumerate(cbs):
        lo=float(b['low']); cl=float(b['close'])
        rlow=min(rlow,lo)
        if rlow<=pl*1.005 and i>=1:
            # 同序号前5日累计vol均值
            vals=[bc[i] if i<len(bc) else (bc[-1] if bc else 0) for bc in base_cums]
            avg=sum(vals)/len(vals) if vals else 0
            cum=cums[i]
            vr=(cum/avg) if avg>0 else 0
            vwap=(amts[i]/cum) if cum>0 else 0
            if vr<=thresh_vr and vwap>0 and cl>vwap:
                return (str(b.get('time') or b.get('trade_time')), cl, round(vr,2))
    return None
def t_plus5(days, daymap, di):
    if di+5>=len(days): return None
    b=day_close(daymap[days[di]]); e=day_close(daymap[days[di+5]])
    return round((e/b-1)*100,2) if b else None
def main():
    evs=json.load(open(os.path.join(DATA,'wolf_tech_entries_stockmap.json'),encoding='utf-8'))
    want={'E05':['liquid'],'E06':['buy_ai_hard'],'E08':['semi_core'],'E12':['buy_semi'],'E13':['guosuan_hw']}
    idx=load_idx()
    rows=[]
    for eid,sides in want.items():
        cfg=evs[eid]; evdk=key(cfg['date'])
        for side in sides:
            for sym in (cfg.get('baskets',{}).get(side) or []):
                dm=load_stock(sym[:6])
                days=sorted(dm.keys())
                if evdk not in days:
                    rows.append({'event':eid,'symbol':sym,'error':'no_5m_data'}); continue
                ei=days.index(evdk)
                lo=max(0,ei-10); hi=min(len(days),ei+11)
                same=None; nearest=None
                for i in range(lo,hi):
                    r=find_254_on(dm,days,i)
                    if r:
                        nearest={'di':i,'date':days[i],'time':r[0],'close':r[1],'vr':r[2]}
                        if days[i]==evdk: same=nearest
                        break  # 取窗口内最近(向前优先? 用第一个即最早日期)
                wolf5=t_plus5(days,dm,ei)
                sys5=t_plus5(days,dm,nearest['di']) if nearest else None
                idx_dump=m5_dump_day(idx.get(evdk) or []) if evdk in idx else None
                rows.append({'event':eid,'wolf_date':cfg['date'],'symbol':sym,'wolf_T5':wolf5,
                             'same_day_254':bool(same),'sys254':nearest,'sys_T5':sys5,
                             'idx_m5_dump_on_wolf_day':idx_dump})
    json.dump(rows,open(os.path.join(DATA,'crowding_pit','wolf_dip254_5m_replay.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('rows',len(rows),flush=True)
    for r in rows:
        n=r.get('sys254'); print('==',r['event'],r['symbol'],'wolf',r['wolf_date'],'wolf5',r['wolf_T5'],
              'same254',int(r['same_day_254']),'sys',(n.get('date') if n else None),'sys5',r['sys_T5'],
              'm5dump',int(bool(r['idx_m5_dump_on_wolf_day'])),flush=True)
    for eid in want:
        rr=[r for r in rows if r['event']==eid]
        w=[r['wolf_T5'] for r in rr if r['wolf_T5'] is not None]
        s=[r['sys_T5'] for r in rr if r.get('sys_T5') is not None]
        print('AGG',eid,'n',len(rr),'same254',sum(r['same_day_254'] for r in rr),'wolf5',round(sum(w)/len(w),2) if w else None,
              'sys5',round(sum(s)/len(s),2) if s else None,'m5dump_days',sum(1 for r in rr if r['idx_m5_dump_on_wolf_day']),flush=True)
    print('DONE',flush=True)
if __name__=='__main__': main()

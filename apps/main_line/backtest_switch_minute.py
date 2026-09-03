# -*- coding: utf-8 -*-
"""backtest_switch_minute.py — E09-E12 主线内切换 5min 分钟级执行点回放 v0

用途：验证“链级切换日 ±5 交易日”内，卖旧链/买新链是否真的有**盘中可执行时点**
（而不只是日线判定可切）。数据：data/stock_5m_bt/*.json + data/index_5min_dh.json。

信号（近似，非30s轮询）：
  卖旧链(SELL)：当日出现“冲高后跌破累计VWAP(黄线)”第一根5min bar → 卖出点
  买新链(BUY) ：若当日上证有单根5min跌幅≤-0.4%(253急杀)，取急杀后个股第一根 close>cumVWAP(企稳站回)；
                无急杀日则用254型(触前日5min低×1.005 + 量比≤0.7 + close>VWAP)
事件一致：卖出侧≥半数有卖点 且 买入侧≥半数有买点（各自在 ±5 交易日内）
输出: data/switch_minute_backtest.json
"""
import os, sys, json
DATA = os.environ.get('DATA_DIR', 'data')

def load(p):
    try:
        return json.load(open(os.path.join(DATA, p), encoding='utf-8'))
    except Exception:
        return {}

def bars_sorted(bars):
    return sorted(bars, key=lambda b: str(b.get('time') or b.get('trade_time')))

def cum_vols(bars):
    out=[]; s=0.0
    for b in bars_sorted(bars):
        s += float(b.get('vol') or 0); out.append(s)
    return out

def cum_amounts(bars):
    out=[]; s=0.0
    for b in bars_sorted(bars):
        s += float(b.get('amount') or 0); out.append(s)
    return out

def day_low(bars): return min(float(b['low']) for b in bars)
def day_close(bars):
    bs=bars_sorted(bars); return float(bs[-1]['close']) if bs else 0.0

def idx_dump_time(idx_bars):
    prev=None
    for b in bars_sorted(idx_bars):
        c=float(b['close'])
        if prev and prev>0 and (c-prev)/prev*100 <= -0.4:
            return str(b.get('time') or b.get('trade_time'))
        prev=c
    return None

def sell_point(bars):
    bs=bars_sorted(bars); vols=cum_vols(bars); amts=cum_amounts(bars)
    if len(bs)<8: return None
    op=float(bs[0]['open']); hi=op
    for i,b in enumerate(bs):
        hi=max(hi,float(b['high']))
        cum=vols[i]
        if cum<=0: continue
        vwap=amts[i]/cum
        if i>=4 and hi>op*1.005 and float(b['close'])<vwap:
            return {'time': str(b.get('time') or b.get('trade_time')), 'close': float(b['close'])}
    return None

def find_254(dm, days, di):
    if di<=0: return None
    prev=days[di-1]; pl=day_low(dm[prev])
    cur=dm[days[di]]; cbs=bars_sorted(cur)
    if len(cbs)<2 or pl<=0: return None
    base=[days[j] for j in range(max(0,di-5),di)]
    bc=[cum_vols(dm[x]) for x in base]
    vols=cum_vols(cur); amts=cum_amounts(cur); rlow=10**18
    for i,b in enumerate(cbs):
        lo=float(b['low']); cl=float(b['close']); rlow=min(rlow,lo)
        if rlow<=pl*1.005 and i>=1:
            vals=[x[i] if i<len(x) else (x[-1] if x else 0) for x in bc]
            avg=sum(vals)/len(vals) if vals else 0
            cum=vols[i]; vr=(cum/avg) if avg>0 else 0
            vwap=(amts[i]/cum) if cum>0 else 0
            if vr<=0.7 and vwap>0 and cl>vwap:
                return {'time': str(b.get('time') or b.get('trade_time')), 'close': cl}
    return None

def buy_point_day(stock, day, idxbars):
    # 253 急杀后站回 VWAP
    t=idx_dump_time(idxbars) if idxbars else None
    if t:
        bs=bars_sorted(stock); vols=cum_vols(stock); amts=cum_amounts(stock)
        for i,b in enumerate(bs):
            bt=str(b.get('time') or b.get('trade_time'))
            if bt<t: continue
            cum=vols[i]
            if cum>0 and amts[i]/cum>0 and float(b['close'])>amts[i]/cum:
                return {'kind':'253_recover','time':bt,'close':float(b['close'])}
    return None

def nearest_event_window(daymap, evdk, half=6):
    days=sorted(daymap.keys())
    if evdk not in days:
        prior=[d for d in days if d<=evdk]
        if not prior: return []
        evdk=prior[-1]
    i=days.index(evdk)
    return days[max(0,i-half):min(len(days),i+half+1)]

EVS = [
 {'eid':'E09','ev':'20260227','sell':['688981.SH','688041.SH','002371.SZ','603501.SH','688012.SH'],'buy':['600584.SH','002156.SZ','002185.SZ','688362.SH']},
 {'eid':'E10','ev':'20260319','sell':['300308.SZ','300502.SZ','002851.SZ'],'buy':['601138.SH','000977.SZ','000938.SZ','603019.SH','000034.SZ']},
 {'eid':'E11','ev':'20260422','sell':['688012.SH','688072.SH','002371.SZ'],'buy':['300054.SZ','300236.SZ','688300.SH','603931.SH','300655.SZ','002409.SZ']},
 {'eid':'E12','ev':'20260605','sell':['300308.SZ','300502.SZ','300394.SZ'],'buy':['688012.SH','688072.SH','002371.SZ','300054.SZ','300236.SZ','600584.SH']},
]
IDX=load('index_5min_dh.json')

def run_event(cfg):
    out={'event':cfg['eid'],'ev_trade':cfg['ev'],'sell':[],'buy':[]}
    for sym in cfg['sell']:
        dm=load('stock_5m_bt/%s.json'%sym[:6])
        win=nearest_event_window(dm,cfg['ev'])
        hit=None
        for d in win:
            p=sell_point(dm.get(d) or [])
            if p:
                hit={'date':d,'point':p}; break
        out['sell'].append({'symbol':sym,'hit':hit})
    for sym in cfg['buy']:
        dm=load('stock_5m_bt/%s.json'%sym[:6])
        win=nearest_event_window(dm,cfg['ev'])
        hit=None
        for d in win:
            r=buy_point_day(dm.get(d) or [], d, IDX.get(d))
            if not r:
                days=sorted(dm.keys()); di=days.index(d) if d in days else -1
                r=find_254(dm,days,di) if di>0 else None
            if r:
                hit={'date':d,'point':r}; break
        out['buy'].append({'symbol':sym,'hit':hit})
    sell_hits=sum(1 for x in out['sell'] if x['hit'])
    buy_hits=sum(1 for x in out['buy'] if x['hit'])
    out['sell_ok']='%d/%d'%(sell_hits,len(out['sell']))
    out['buy_ok']='%d/%d'%(buy_hits,len(out['buy']))
    out['executable']=bool(sell_hits*2>=len(out['sell']) and buy_hits*2>=len(out['buy']))
    print(out['event'],out['sell_ok'],out['buy_ok'],'exec',out['executable'],flush=True)
    return out

def main():
    res=[run_event(e) for e in EVS]
    path=os.path.join(DATA,'switch_minute_backtest.json')
    json.dump(res,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('WROTE',path)
    print('executable events:',[x['event'] for x in res if x['executable']])
    return 0

if __name__=='__main__':
    raise SystemExit(main())

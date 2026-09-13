# -*- coding: utf-8 -*-
"""backtest_wolf_dip_254.py — 底仓视角 v0：用日线近似 254(A档触前日低点+缩量)
对比: Wolf 事件日低吸 vs 系统 254 触发日买入的 T+N 收益。
目的: 回答"如果我们也有底仓，是否会在狼大低吸时点同样执行低吸、收益差多少"
注意: 日线代理(非5min), vol_ratio=当日量/前20日均量(粗), 只测 buy_* / liquid / 特殊侧。
用法: python -u apps/main_line/backtest_wolf_dip_254.py
"""
import os, sys, json, urllib.request, gzip, time, datetime as _dt
# 2026-09-13: 取数走 core/tushare_relay.py（datahubco+promax），旧 TUSHARE_API_URL 已废弃
def _relay():
    """加载 core/tushare_relay.py（2026-09-13 起 datahubco 基础接口 + promax 聚合接口，
    替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib, sys
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for _p in pathlib.Path(__file__).resolve().parents:
        if (_p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(_p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def call(api, params, fields=""):
    """Tushare 中继查询（返回 items 行列表；中继内部含重试/分页/双源降级）。"""
    _fields, items = _relay().relay_items(api, fields=fields, **(params or {}))
    return items or []
_CACHE={}
def daily(sym):
    if sym in _CACHE: return _CACHE[sym]
    it=call('daily',{'ts_code':sym,'start_date':'20240601','end_date':'20260815'},'ts_code,trade_date,open,close,high,low,vol')
    out=[]
    for r in it:
        try: out.append({'d':str(r[1]),'o':float(r[2]),'c':float(r[3]),'h':float(r[4]),'l':float(r[5]),'v':float(r[6])})
        except Exception: pass
    out.sort(key=lambda x:x['d'])
    _CACHE[sym]=out
    return out
def idx(ds):
    return [i for i,x in enumerate(daily if False else []) if False]
def trigger_day(sym, upto=None, window=None):
    """在 event 前后 window 交易日窗口内找第一个满足 254 近似 的日期: 当日low<=前日low*1.005 且 当日vol<=前20日均量*0.7"""
    rows=daily(sym)
    # rows list asc; window indexes
    return None
def row_after(rows, upto, n):
    rs=[x for x in rows if x['d']<=upto]
    return rs[-n] if len(rs)>=n else None
def find_254(sym, evd, half=10):
    rows=daily(sym)
    if len(rows)<40: return None
    evi=[i for i,x in enumerate(rows) if x['d']<=evd]
    if not evi: return None
    i0=evi[-1]
    lo=max(0,i0-half); hi=min(len(rows), i0+half+1)
    for i in range(lo,hi):
        if i<1: continue
        x=rows[i]; pv=rows[i-1]
        avg=sum(r['v'] for r in rows[max(0,i-21):i])/max(1,min(20,i))
        if avg<=0: continue
        vr=x['v']/avg
        if x['l']<=pv['l']*1.005 and vr<=0.7:
            return {'trigger_date':x['d'],'event_offset':i-i0,'vr':round(vr,2)}
    return None
def ret_after(sym, buy_d, hold=5):
    rows=daily(sym)
    bi=[i for i,x in enumerate(rows) if x['d']==buy_d]
    if not bi: return None
    i=bi[0]
    if i+hold>=len(rows): return None
    b=rows[i]['c']; e=rows[i+hold]['c']
    return round((e/b-1)*100,2) if b else None
def main():
    evs=json.load(open(os.path.join(DATA,'wolf_tech_entries_stockmap.json'),encoding='utf-8'))
    want={'E05':['liquid'],'E06':['buy_ai_hard'],'E08':['semi_core'],'E12':['buy_semi'],'E13':['guosuan_hw']}
    rows=[]
    for eid,sides in want.items():
        cfg=evs[eid]; evd=cfg['date'].replace('-','')
        for side in sides:
            for sym in (cfg.get('baskets',{}).get(side) or []):
                t=find_254(sym,evd)
                tr=t['trigger_date'] if t else None
                w5=ret_after(sym,evd)
                s5=ret_after(sym,tr) if tr else None
                rows.append({'event':eid,'date':cfg['date'],'side':side,'symbol':sym,
                             'wolf_evt_date':evd,'system_254':t,'wolf_T5':w5,'system_254_T5':s5,
                             'aligned_same_day':bool(t and t['trigger_date']==evd),
                             'offset_days':t['event_offset'] if t else None})
    json.dump(rows, open(os.path.join(DATA,'crowding_pit','wolf_dip254_backtest.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print('rows',len(rows),flush=True)
    for r in rows:
        t=r['system_254']; ts=t['trigger_date'] if t else '无触发'
        print('==',r['event'],r['symbol'],'wolf',r['wolf_evt_date'],'254',ts,
              'same_day',int(r['aligned_same_day']),'offset',r['offset_days'],
              '| wolf_T5',r['wolf_T5'],'sys_T5',r['system_254_T5'],flush=True)
    # aggregate
    for eid in want:
        rr=[r for r in rows if r['event']==eid]
        same=sum(r['aligned_same_day'] for r in rr)
        w=[r['wolf_T5'] for r in rr if r['wolf_T5'] is not None]
        s=[r['system_254_T5'] for r in rr if r['system_254_T5'] is not None]
        print('AGG',eid,'n',len(rr),'same_day',same,'wolf_mean5',round(sum(w)/len(w),2) if w else None,
              'sys_mean5',round(sum(s)/len(s),2) if s else None,flush=True)
    print('DONE',flush=True)
if __name__=='__main__':
    main()

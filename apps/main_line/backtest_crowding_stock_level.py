# -*- coding: utf-8 -*-
"""backtest_crowding_stock_level.py — Phase2: E06-E13 个股级PIT拥挤 vs 旧整概念黑名单
输入: data/wolf_tech_entries_stockmap.json(事件篮子) + data/crowding_pit/stock_crowd_<date>.json + stock_concept_map(DB)
      + data/rotation_quadrant_history.json(旧口径拥挤无空间, 近似)
输出: data/crowding_pit/stock_level_recheck.json + docs/crowding-stocklevel-event-recheck.md(自动段)
用法: python -u apps/main_line/backtest_crowding_stock_level.py
"""
import os, sys, json, urllib.request, gzip, time, collections, datetime as _dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import position_class as pc
except Exception as e:
    print('WARN position_class import:', e, flush=True); pc=None

TOKEN=os.getenv('TUSHARE_TOKEN',''); URL=os.getenv('TUSHARE_API_URL','')
DB=os.getenv('DATABASE_URL',''); DATA=os.environ.get('DATA_DIR','data')
try:
    import psycopg2
except Exception:
    psycopg2=None

ROT_GROUP = {'E06':'光通信','E07':'芯片/半导体','E08':'芯片/半导体','E09':'芯片/半导体',
             'E10':'国算/算力','E11':'材料','E12':'芯片/半导体','E13':'国算/算力'}

def call(api, params, fields):
    body={'api_name':api,'token':TOKEN,'params':params,'fields':fields}
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept-Encoding':'identity'})
    with urllib.request.urlopen(req,timeout=60) as resp: raw=resp.read()
    try: d=json.loads(raw.decode())
    except UnicodeDecodeError: d=json.loads(gzip.decompress(raw).decode())
    if d.get('code')!=0: raise RuntimeError('%s %s' % (api, str(d.get('msg'))[:120]))
    return d.get('data',{}).get('items') or []

_KL={}
def kline(sym, force=False):
    if sym in _KL and not force: return _KL[sym]
    start='20240101'; end='20260901'
    dl=call('daily',{'ts_code':sym,'start_date':start,'end_date':end},'ts_code,trade_date,close,vol')
    af=call('adj_factor',{'ts_code':sym,'start_date':start,'end_date':end},'ts_code,trade_date,adj_factor')
    adj={str(r[1]):float(r[2]) for r in af}
    out=[]
    for r in dl:
        d=str(r[1]); close=float(r[2]); a=adj.get(d)
        if a is None: continue
        out.append({'date':d,'close':close,'adj':a})
    out.sort(key=lambda x:x['date'])
    _KL[sym]=out
    return out

def qfq_at(sym, upto):
    rows=[r for r in kline(sym) if r['date']<=upto]
    if not rows: return None
    last_adj=rows[-1]['adj']
    import pandas as pd
    idx=pd.to_datetime([r['date'] for r in rows], format='%Y%m%d')
    vals=[r['close']*r['adj']/last_adj for r in rows]
    return pd.Series(vals, index=idx)

def fwd_ret(sym, upto, n=20):
    rows=[r for r in kline(sym) if r['date']>upto]
    if len(rows)<n: return None
    b0=rows[0]['close']; f=rows[n-1]['close']
    if not b0: return None
    return round((f/b0-1)*100,2)

def stock_pos_features(sym, upto):
    ser=qfq_at(sym, upto)
    if ser is None or len(ser)<30: return None,None
    f=pc.position_features(ser)
    if f is None: return None,None
    st=pc.structure_of(ser)
    f['structure']=st.get('desc','flat')
    cls=pc.classify(f)
    return cls['position'], f

def space_flag(pos, f):
    vm60=f.get('vs_ma60_pct') or 0; r20=f.get('r20')
    if pos=='LOW': return True,'LOW(空间)'
    if pos=='MID' and vm60>0 and r20 is not None and -10<=r20<0: return True,'MID回踩(空间)'
    return False,pos

def load_stock_concepts():
    if not psycopg2: return {}
    conn=psycopg2.connect(DB); cur=conn.cursor()
    cur.execute('SELECT ts_code, concept_name FROM stock_concept_map')
    d=collections.defaultdict(list)
    for s,c in cur.fetchall(): d[str(s)].append(str(c))
    cur.close(); conn.close(); return d

def nearest_q(qdates, d):
    dd=d.replace('-','')
    prev=[q for q in qdates if q<=dd]
    return max(prev) if prev else None

def main():
    events=json.load(open(os.path.join(DATA,'wolf_tech_entries_stockmap.json'),encoding='utf-8'))
    quad=json.load(open(os.path.join(DATA,'rotation_quadrant_history.json'),encoding='utf-8'))
    QD=quad['dates']; QH=quad['history']
    stock_concepts=load_stock_concepts()
    print('stock_concept_map loaded',len(stock_concepts),flush=True)
    rows=[]; cache_miss=[]
    ids=[e for e in events if e.startswith('E') and e in ('E06','E07','E08','E09','E10','E11','E12','E13')]
    for eid in ids:
        cfg=events[eid]; d=cfg['date']; ds=d.replace('-','')
        snap=json.load(open(os.path.join(DATA,'crowding_pit','stock_crowd_%s.json'%d),encoding='utf-8'))
        stock=snap.get('stock',{})
        qd=nearest_q(QD,d); h=QH.get(qd) or {}
        rotg=ROT_GROUP[eid]; rotq=h.get(rotg) or {'quadrant':'n/a'}
        old_crowd=(rotq.get('quadrant')=='拥挤无空间')
        for side, bs in cfg.get('baskets',{}).items():
            if not isinstance(bs,list): continue
            if not (side.startswith('buy') or side in ('semi_equip','semi_core','packaging_low','materials','guosuan_hw')):
                # sell-side(E10/E12 sell_) 与 ETF 串不参与"是否被拦"计分
                continue
            for sym in bs:
                if sym not in stock and sym not in stock_concepts:
                    pass
                v=stock.get(sym) or {}
                pos,f=stock_pos_features(sym, ds)
                if f is None:
                    cache_miss.append((eid,sym)); continue
                have_space,space_desc=space_flag(pos,f)
                nf=int(v.get('n_funds') or 0); fl=float(v.get('sum_float') or 0)
                # old: 概念整组拥挤即拦(与现线上逻辑近似)
                # 旧逻辑 = 事件方向拥挤无空间 => 该方向整概念成分拦(与现线上整包blacklist一致)
                old_block = old_crowd
                # new v1
                crowd_core = nf>=4 and fl>=1.0
                new_block = bool(crowd_core and pos in ('HIGH','MID') and not have_space)
                block_reason=[]
                if crowd_core: block_reason.append('crowd:n%d/f%.2f%%'%(nf,fl))
                if pos: block_reason.append('pos:'+pos)
                if not have_space: block_reason.append('no_space')
                else: block_reason.append('space:'+space_desc)
                rows.append({'event':eid,'date':d,'side':side,'symbol':sym,'quad_group':rotg,'quad':rotq.get('quadrant'),
                             'old_block':old_block,'new_block':new_block,'n_funds':nf,'float_pct':round(fl,2),
                             'mkv_yi':v.get('sum_mkv_yi'),'position':pos,'structure':(f or {}).get('structure'),
                             'space':space_desc,'fwd20':fwd_ret(sym,ds),'reasons':block_reason})
    json.dump(rows, open(os.path.join(DATA,'crowding_pit','stock_level_recheck.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print('rows',len(rows),'price_miss',len(cache_miss),cache_miss[:10],flush=True)
    # summary base
    for eid in ids:
        rr=[r for r in rows if r['event']==eid]
        ob=sum(r['old_block'] for r in rr); nb=sum(r['new_block'] for r in rr)
        print('==',eid,rr[0]['date'] if rr else '?','n',len(rr),'old_block',ob,'new_block',nb,flush=True)
        for r in rr:
            print('   %-10s %-6s old=%s new=%s pos=%s n=%d f=%.2f fwd20=%s %s'%(r['symbol'],r['side'][:6],int(r['old_block']),int(r['new_block']),r.get('position'),r['n_funds'],r['float_pct'],r.get('fwd20'),r.get('space')),flush=True)
    print('DONE',flush=True)

if __name__=='__main__':
    main()

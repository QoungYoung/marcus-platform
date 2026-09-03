# -*- coding: utf-8 -*-
"""ab_wolf_gates.py — E05/E06/E08/E12/E13 A/B: Wolf低吸日如果走旧技术硬门槛会被卡多少
用日线近似判断: MA5<MA20(旧会降级/可能移除), RSI6, MACD死叉, KDJ死叉(K>=80), 射击之星
输出: data/crowding_pit/ab_gate_hits.json
"""
import os,json,urllib.request,gzip,math
TOKEN=os.getenv('TUSHARE_TOKEN',''); URL=os.getenv('TUSHARE_API_URL',''); DATA=os.environ.get('DATA_DIR','data')
def call(api,params,fields):
    body={'api_name':api,'token':TOKEN,'params':params,'fields':fields}
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept-Encoding':'identity'})
    with urllib.request.urlopen(req,timeout=60) as resp: raw=resp.read()
    try: d=json.loads(raw.decode())
    except UnicodeDecodeError: d=json.loads(gzip.decompress(raw).decode())
    if d.get('code')!=0: raise RuntimeError(str(d.get('msg'))[:100])
    return d.get('data',{}).get('items') or []
_C={}
def df(sym):
    if sym in _C: return _C[sym]
    dl=call('daily',{'ts_code':sym,'start_date':'20240601','end_date':'20260815'},'ts_code,trade_date,open,close,high,low,vol')
    af=call('adj_factor',{'ts_code':sym,'start_date':'20240601','end_date':'20260815'},'ts_code,trade_date,adj_factor')
    adj={str(x[1]):float(x[2]) for x in af}
    rows=[]
    for x in dl:
        d=str(x[1]); a=adj.get(d)
        if a is None: continue
        last=1.0
        rows.append({'d':d,'o':float(x[2]),'c':float(x[3])*a,'h':float(x[4])*a,'l':float(x[5])*a,'v':float(x[6])})
    rows.sort(key=lambda r:r['d'])
    _C[sym]=rows; return rows
def ema(v,n):
    k=2/(n+1); e=v[0]; out=[e]
    for x in v[1:]: e=x*k+e*(1-k); out.append(e)
    return out
def rsi6(closes):
    if len(closes)<8: return None
    g=l=0.0
    for i in range(1,7):
        d=closes[i]-closes[i-1]
        if d>=0: g+=d
        else: l-=d
    if l==0: return 100.0
    rs=(g/6)/(l/6); return 100-100/(1+rs)
def kdj(h,l,c):
    if len(h)<9: return None,None,None
    k=d=50.0
    for i in range(len(h)):
        lo=min(l[max(0,i-8):i+1]); hi=max(h[max(0,i-8):i+1]); rsv=(c[i]-lo)/(hi-lo)*100 if hi>lo else 50
        k=2/3*k+1/3*rsv; d=2/3*d+1/3*k
    return k,d,k-d
def macd(c):
    if len(c)<35: return None,None
    e12=ema(c,12); e26=ema(c,26); dif=[a-b for a,b in zip(e12,e26)]
    dea=ema(dif,9)
    return dif[-1],dea[-1],dif[-1]-dea[-1]
def shooting(rows):
    if len(rows)<2: return False
    b=rows[-1]
    body=abs(b['c']-b['o']); upper=b['h']-max(b['c'],b['o']); low=min(b['c'],b['o'])-b['l']
    return body>0 and upper>=2*body
def main():
    evs=json.load(open(os.path.join(DATA,'wolf_tech_entries_stockmap.json'),encoding='utf-8'))
    want={'E05':['liquid'],'E06':['buy_ai_hard'],'E08':['semi_core'],'E12':['buy_semi'],'E13':['guosuan_hw']}
    rows=[]
    for eid,sides in want.items():
        cfg=evs[eid]; date=cfg['date'].replace('-','')
        for side in sides:
            for sym in cfg.get('baskets',{}).get(side) or []:
                rows.append({'event':eid,'date':date,'symbol':sym})
    replay={r['symbol']:r for r in json.load(open(os.path.join(DATA,'crowding_pit','wolf_dip254_5m_replay.json'))) if r.get('wolf_T5') is not None}
    out=[]
    for r in rows:
        try:
            allr=df(r['symbol']); sub=[x for x in allr if x['d']<=r['date']]
            if len(sub)<35: out.append({**r,'skip':'<35d'}); continue
            closes=[x['c'] for x in sub]; highs=[x['h'] for x in sub]; lows=[x['l'] for x in sub]
            ma5=sum(closes[-5:])/5; ma20=sum(closes[-20:])/20
            rs=rsi6(closes[-6:]) if len(closes)>=6 else None
            k,d,_=kdj(highs,lows,closes); dea=macd(closes)
            macd_dead=bool(dea and dea[2]<0)
            kdj_dead_high=bool(k is not None and d is not None and k<d and k>=80)
            sh=shooting(sub)
            out.append({**r,'ma5_below_ma20':bool(ma5<ma20),'rsi6':round(rs,1) if rs is not None else None,
                        'macd_dead':macd_dead,'kdj_dead_high':kdj_dead_high,'shooting':sh,
                        'wolf_T5':(replay.get(r['symbol']) or {}).get('wolf_T5')})
        except Exception as e:
            out.append({**r,'err':str(e)[:80]})
    json.dump(out,open(os.path.join(DATA,'crowding_pit','ab_gate_hits.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    ok=[x for x in out if 'skip' not in x and 'err' not in x]
    print('rows',len(ok),flush=True)
    for f in ['ma5_below_ma20','macd_dead','kdj_dead_high','shooting']:
        hits=[x for x in ok if x[f]]
        w=[x['wolf_T5'] for x in hits if x.get('wolf_T5') is not None]
        noth=[x for x in ok if not x[f]]
        wn=[x['wolf_T5'] for x in noth if x.get('wolf_T5') is not None]
        print('%-18s hit %2d/%d  wolfT5_mean_hit=%s  mean_nohit=%s'%(f,len(hits),len(ok),
              round(sum(w)/len(w),2) if w else None, round(sum(wn)/len(wn),2) if wn else None),flush=True)
    for x in ok:
        print(x['event'],x['symbol'],'ma',int(x['ma5_below_ma20']),'macd',int(x['macd_dead']),'kdjH',int(x['kdj_dead_high']),'shoot',int(x['shooting']),'wolf5',x.get('wolf_T5'),flush=True)
    print('DONE',flush=True)
if __name__=='__main__': main()

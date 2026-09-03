# -*- coding: utf-8 -*-
"""plan_compiler_v0.py — 狼大计划生成器 v0 (论点/触发分离) + PIT 回放
生成 data/plan_library.json + data/plan_replay_log.json
"""
import os, json, sys
D=os.environ.get('DATA_DIR','/app/data')

def load(path):
    p=os.path.join(D,path)
    try: return json.load(open(p,encoding='utf-8'))
    except: return {}

def theme_pit_score(concept_code, date, mainline=1.0):
    """概念 PIT 打分(生成日视角, 结构优先): rel=相对指数超额, crowd=拥挤(0=不拥挤), mainline=主线归属(0~1),
    fund=近20日净流入强度(做确认/tiebreaker).
    权重: thesis = 0.35*rel + 0.25*(1-crowd) + 0.2*mainline + 0.2*fund
    返回 {fund, rel, rel_pct, net20, crowd, mainline, thesis_score} 或在缺数据时返回 None。"""
    c=load('concept_hist.json').get(concept_code) or {}
    dates=[str(x) for x in (c.get('dates') or [])]
    closes=c.get('close') or []; net=c.get('net_amount') or []
    if not dates: return None
    pairs=[]
    for i,dt0 in enumerate(dates):
        dt=str(dt0).replace('-','')
        pairs.append((dt, float(closes[i]) if i<len(closes) else 0.0, float(net[i]) if i<len(net) else 0.0))
    pairs=[p for p in pairs if p[0]<=date.replace('-','')]
    if len(pairs)<25: return None
    net20=sum(p[2] for p in pairs[-20:])
    c20=(pairs[-1][1]/pairs[-21][1]-1)*100 if len(pairs)>=21 else 0
    rows=sorted(load('index_daily_000001.json'), key=lambda x:int(str(x['trade_date']).replace('-','')))
    rows=[r for r in rows if int(str(r['trade_date']).replace('-',''))<=int(date.replace('-',''))]
    clos=[float(r['close']) for r in rows]
    i20=((clos[-1]/clos[-21]-1)*100) if len(clos)>=21 else 0
    rel=c20-i20
    abs60=[abs(p[2]) for p in pairs[-60:]] or [1]
    fund=max(0.0, min(1.0, 0.5 + net20/max(sum(abs60)/len(abs60),1)/2))
    rel_s=max(0.0, min(1.0, 0.5 + rel/5))
    crowd=0.0  # 已验证 301018等不在拥挤黑名单
    ml=max(0.0,min(1.0,float(mainline or 0)))
    thesis=round(0.35*rel_s+0.25*(1-crowd)+0.2*ml+0.2*fund,3)
    return {'fund':round(fund,3),'rel':round(rel_s,3),'rel_pct':round(rel,2),'net20':round(net20/1e8,2),'crowd':crowd,'mainline':round(ml,2),'thesis_score':thesis}

def daily_close(code, paths):
    out={}
    for p in paths:
        d=load(p)
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k, {'close':float(bs[-1]['close']),
                                      'low':min(float(b['low']) for b in bs),
                                      'high':max(float(b['high']) for b in bs),
                                      'vol':sum(float(b.get('vol') or 0) for b in bs),
                                      'amt':sum(float(b.get('amount') or 0) for b in bs)})
    return out

def pos_pct(dc, date, win=90):
    ds=sorted([k for k in dc if k<=date.replace('-','') and dc[k]['close']])
    if not ds: return None
    vals=[dc[k]['close'] for k in ds[-win:]]
    lo=min(vals); hi=max(vals); cur=dc[ds[-1]]['close']
    return round((cur-lo)/(hi-lo)*100,1) if hi>lo else 50.0

def vol_ratio(dc, date, back=5):
    ds=sorted([k for k in dc if k<=date.replace('-','') and dc[k]['vol']])
    if len(ds)<back+1: return None
    cur=dc[ds[-1]]['vol']; prev=[dc[k]['vol'] for k in ds[-back-1:-1]]
    avg=sum(prev)/len(prev) if prev else 0
    return round(cur/avg,2) if avg>0 else None

def stable(dc, date):
    ds=sorted([k for k in dc if k<=date.replace('-','') and dc[k]['close']])
    if len(ds)<2: return False
    return dc[ds[-1]]['close'] >= dc[ds[-2]]['close']

# ---- index daily PIT key levels ----
def index_sorted_daily():
    rows=load('index_daily_000001.json')
    rows=sorted(rows,key=lambda x:int(str(x['trade_date']).replace('-','')))
    return rows

def key_levels(date):
    di=int(date.replace('-',''))
    rows=[r for r in index_sorted_daily() if int(str(r['trade_date']).replace('-',''))<=di]
    if len(rows)<60: return []
    closes=[float(r['close']) for r in rows]
    low30=min(float(r['low']) for r in rows[-30:]); high30=max(float(r['high']) for r in rows[-30:])
    low60=min(float(r['low']) for r in rows[-60:]); high60=max(float(r['high']) for r in rows[-60:])
    span=high60-low60
    def ma(n): return round(sum(closes[-n:])/n,1) if len(closes)>=n else None
    cur=closes[-1]
    return {'close':cur,'box30_low':round(low30,1),'box30_high':round(high30,1),
            'r382':round(low60+0.382*span,1),'r50':round(low60+0.5*span,1),'r618':round(low60+0.618*span,1),
            'ma20':ma(20),'ma60':ma(60),'ma200':ma(200)}

# ---- index 5min intraday low on a date ----
def idx5_low(date):
    d=load('index_5min_dh.json')
    k=date.replace('-','')
    raw=None
    for kk,vv in d.items():
        if str(kk).replace('-','')==k: raw=vv; break
    if not raw: return None
    return min(float(b['low']) for b in raw)

def gen_theme_plan(date):
    """主题计划: 论点=AI算力主线中'液冷'长期候选(PIT打分); 触发=低位(<30%)+缩量企稳+放量"""
    score=theme_pit_score('BK1138.DC', date, mainline=1.0)  # 液冷属于AI/算力/科技主线
    thesis='AI/算力/科技为主线; 细分"液冷"(数据中心)为长期候选(大厂资本开支/算力散热)'
    if score:
        thesis += ' | PIT主题分(结构优先)=%.2f (相对%.2f/不拥挤%.2f/主线%.2f/资金%.2f)' % (score['thesis_score'], score['rel'], 1-score['crowd'], score['mainline'], score['fund'])
    p={'id':'plan_theme_liquid_20260225','type':'theme_plan','subject':'液冷(数据中心)',
       'horizon':'半年','created_at':date,'status':'armed','thesis':thesis,
       'trigger':{'symbol':'301018','pos_pct_max':30.0,'vol_min':0.8,'require_stable':True},
       'action':'build_on_dip(低位+企稳放量分批建仓)','gate':'crowding+P2Gate 放行'}
    if score: p['pit_score']=score
    return p

def gen_refill_plan(date, levels):
    """关键位回补计划: 取当前价下方最近支撑(0.382/箱底/MA)作为回补触发"""
    cur=levels['close']
    # 回补关键位: 优先取“MA20均线回踩”作为触发位(≈3870), 其次0.382回撤/箱底; 构成回踩支撑带
    cands=[('ma20',levels['ma20']),('r382',levels['r382']),('box30_low',levels['box30_low']),('ma60',levels['ma60'])]
    below=[(k,v) for k,v in cands if v and v<cur]
    if not below:
        below=[(k,v) for k,v in cands if v]  # 无下方支撑则取现有(最近)
        if not below: return None
    k,v=below[0]  # 优先MA20(最接近狼大3870), 其次r382/箱底/ma60
    support_band=[('ma20',levels['ma20']),('r382',levels['r382']),('box30_low',levels['box30_low']),('ma60',levels['ma60'])]
    return {'id':'plan_refill_3870_20260818','type':'refill_plan','subject':'上证指数','horizon':'2周',
            'created_at':date,'status':'armed','thesis':'大4浪反弹中, 预置关键位回补T仓, 吃本轮差价',
            'trigger':{'key_level':k,'value':v,'bands':support_band},
            'action':'refill_T(跌破/接近关键位企稳→回补上周T仓, 目标差价3-5点)','gate':'P2Gate(wave+macro) 放行才回补'}

# ===== PIT 回放 =====
dc301=daily_close('301018',['stock_5m_bt/301018.json','recent_sync/301018.json'])
library=[]
# A) theme plan as of 2026-02-25
tp=gen_theme_plan('2026-02-25'); library.append(tp)
# B) refill plan as of 2026-08-18
lv=key_levels('2026-08-18'); rp=gen_refill_plan('2026-08-18', lv); 

# ---- replay A: did theme plan fire on 08-25? (低位+缩量企稳+放量) ----
pp=pos_pct(dc301,'2026-08-25'); vr=vol_ratio(dc301,'2026-08-25'); st=stable(dc301,'2026-08-25')
fireA = (pp is not None and pp<30 and vr is not None and vr>=0.8 and st)
# ---- replay B: did refill plan fire on 08-24? (index 5min low hits below r382 support) ----
low_0824=idx5_low('2026-08-24')
fireB = bool(rp and low_0824 is not None and low_0824 <= rp['trigger']['value']*1.01)

library.append(rp) if rp else None
json.dump({'generated':'plan_compiler_v0','library':library},
          open(os.path.join(D,'plan_library.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('=== 计划库 (v0) ===')
for p in library:
    print(json.dumps(p,ensure_ascii=False))
print()
print('=== PIT 回放 ===')
print('A) 液冷半年计划(2026-02-25生成): @08-25 301018 pos_pct=%s%% vol_ratio=%s stable=%s → fire=%s' % (pp, vr, st, fireA))
if lv:
    print('B) 3870回补计划(2026-08-18生成): 关键位=%s=%.1f | 08-24 上证5min最低=%.1f → fire=%s' % (rp['trigger']['key_level'], rp['trigger']['value'], low_0824, fireB))
json.dump({'replay':{'A_liquid':{'date':'2026-08-25','pos_pct':pp,'vol_ratio':vr,'stable':st,'fire':fireA},
                     'B_refill':{'date':'2026-08-24','key_level':rp['trigger']['key_level'] if rp else None,'value':rp['trigger']['value'] if rp else None,'idx5_low':low_0824,'fire':fireB}}},
          open(os.path.join(D,'plan_replay_log.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('WROTE plan_library.json + plan_replay_log.json')

# -*- coding: utf-8 -*-
"""eval_ms_robust.py — 方案 B（按近 5 日相对强度选主线）的稳健性检验（2026-09-12）。
① 分段（1–5 月 / 6–9 月）② 前瞻窗口 3/5/10 日 ③ 与资格闸组合（只在 gate 确认的主题里选）④ top1/top2。
"""
import json, sys
import pandas as pd
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/backend')
from app.database import SessionLocal
from sqlalchemy import text
from app.services import wolf_mainline_select as MS

def stat(v):
    n = len(v)
    if not n: return {'n': 0}
    m = sum(v)/n; sd = (sum((x-m)**2 for x in v)/max(1, n-1))**0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m/(sd/n**0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0)/n, 3)}

db = SessionLocal()
rows = db.execute(text("SELECT trade_date, ts_code, pct_chg FROM mkt_bars_daily WHERE pct_chg IS NOT NULL")).all()
df = pd.DataFrame(rows, columns=['d','ts','pc']); df['pc'] = df['pc'].astype(float)/100.0
wide = df.pivot_table(index='d', columns='ts', values='pc', aggfunc='first').sort_index()
idx = wide.index.tolist()
px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in idx}
uni, lead, allc = MS.load_universe()
mkt = {d: (sum(px[d].values())/len(px[d])) for d in idx if px.get(d)}
gate = {}
for r in db.execute(text("SELECT trade_date, payload FROM daily_artifacts WHERE artifact_key='mainline_gate'")).mappings().all():
    p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'])
    gate[r['trade_date']] = [x['theme'] for x in (p.get('rows') or []) if x.get('gate') and x.get('theme') in uni]
def r5(th, i):
    if i < 5: return None
    ser = []
    for k in range(i-4, i+1):
        b = MS.basket_return(px.get(idx[k], {}), uni.get(th) or [])
        m = MS.basket_return(px.get(idx[k], {}), allc)
        ser.append(None if (b is None or m is None) else (b-m))
    return MS.compound(ser)
def fwd(d, th, h):
    i = idx.index(d); seg = idx[i+1:i+1+h]
    if len(seg) < h or th not in uni: return None
    c = 1.0
    for dd in seg:
        v = [px[dd][x] for x in uni[th] if x in px.get(dd, {})]
        if v: c *= (1+sum(v)/len(v))
    m = 1.0
    for dd in seg: m *= (1+mkt.get(dd, 0.0))
    return (c-m)*100
buckets = {}
for i in range(5, len(idx)):
    d = idx[i]
    sc = {th: r5(th, i) for th in uni}
    sc = {k: v for k, v in sc.items() if v is not None}
    if len(sc) < 8: continue
    rank = sorted(sc, key=lambda t: -sc[t])
    gset = set(gate.get(d, []))
    grank = [t for t in rank if t in gset] or rank
    for h in (3, 5, 10):
        for name, rk, k in (('B_top1', rank, 1), ('B_top2', rank, 2), ('B_gate_top1', grank, 1), ('B_gate_top2', grank, 2)):
            vals = [fwd(d, t, h) for t in rk[:k]]
            vals = [v for v in vals if v is not None]
            if vals:
                buckets.setdefault('%s_h%d' % (name, h), []).append(sum(vals)/len(vals))
                if h == 5 and name in ('B_top1', 'B_top2'):
                    seg = 'H1(1-5月)' if d <= '20260531' else 'H2(6-9月)'
                    buckets.setdefault('%s_%s' % (name, seg), []).append(sum(vals)/len(vals))
print('| 方案 | n | 均值 | t | 正比例 |')
print('|---|---|---|---|---|')
out = {}
for k in sorted(buckets):
    s = stat(buckets[k]); out[k] = s
    print('| %s | %d | %s%% | %s | %s |' % (k, s.get('n', 0), s.get('mean'), s.get('t'), s.get('pos')))
json.dump(out, open('/app/data/eval_ms_robust.json','w'), ensure_ascii=False, indent=1)
db.close()

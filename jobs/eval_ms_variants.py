# -*- coding: utf-8 -*-
"""eval_ms_variants.py — 方向层设计的变体扫描（2026-09-12）。

一次加载数据，对多个**经济含义明确**的方案（不是大网格）计算 top1/top3 后 5 日超额，
并给出"他的方向落在我们 topN"的比例（随机基线 top1=7.7%、top3=23%）。
方案（都由他的话支撑）：
  A accel_only       ：只按"谁在动"（近5日超额−前5日超额）
  B r5_only          ：只按"有没有行情"（近5日超额）
  C accel+r5         ：两者等权
  D C+breadth        ：加参与面扩散
  E D+位置分层        ：高位(20日>+5%)给 r5 更高权重、低位给 accel 更高权重（他：高位只做动量确认）
  F E+带动板块闸      ：带动板块(券商/银行)弱 → 高位方向降权（他：券商不表现就卡住）
"""
import json, os, sys, collections
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

def main():
    db = SessionLocal()
    rows = db.execute(text("SELECT trade_date, ts_code, pct_chg FROM mkt_bars_daily WHERE pct_chg IS NOT NULL")).all()
    df = pd.DataFrame(rows, columns=['d','ts','pc']); df['pc'] = df['pc'].astype(float)/100.0
    wide = df.pivot_table(index='d', columns='ts', values='pc', aggfunc='first').sort_index()
    idx = wide.index.tolist()
    px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in idx}
    uni, lead, allc = MS.load_universe()
    ma20 = wide.rolling(20).mean(); above = (wide > ma20)
    breadth = {}
    for d in idx:
        row = above.loc[d]
        breadth[d] = {th: (sum(1 for c in uni[th] if bool(row.get(c, False))) / max(1, len(uni[th]))) for th in uni}
    dmap = {r['dir_text']: r['theme'] for r in db.execute(text("SELECT dir_text, theme FROM wolf_dir_theme_map")).mappings().all()}
    his = {}
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s: his[r['trade_date']] = s
    mkt = {d: (sum(px[d].values())/len(px[d])) for d in idx if px.get(d)}
    def win(th, i, lo, hi):
        ser = []
        for k in range(lo, hi+1):
            if 0 <= k < len(idx):
                b = MS.basket_return(px.get(idx[k], {}), uni.get(th) or [])
                m = MS.basket_return(px.get(idx[k], {}), allc)
                ser.append(None if (b is None or m is None) else (b - m))
        return MS.compound(ser)
    def fwd(d, th, h=5):
        i = idx.index(d); seg = idx[i+1:i+1+h]
        if len(seg) < h or th not in uni: return None, None
        c = 1.0
        for dd in seg:
            v = [px[dd][x] for x in uni[th] if x in px.get(dd, {})]
            if v: c *= (1 + sum(v)/len(v))
        m = 1.0
        for dd in seg: m *= (1 + mkt.get(dd, 0.0))
        i0 = max(0, i-20); c0 = 1.0
        for dd in idx[i0:i]:
            v = [px[dd][x] for x in uni[th] if x in px.get(dd, {})]
            if v: c0 *= (1 + sum(v)/len(v))
        return (c-m)*100, (c0-1)*100
    # 预算：把每天需要的量算一次
    P = {}
    for i in range(12, len(idx)):
        d = idx[i]
        r5 = {th: win(th, i, i-4, i) for th in uni}
        r5p = {th: win(th, i, i-9, i-5) for th in uni}
        accel = {th: (r5[th]-r5p[th]) if (r5.get(th) is not None and r5p.get(th) is not None) else None for th in uni}
        r20 = {th: win(th, i, i-19, i) for th in uni}
        brd = {}
        for th in uni:
            cur = (breadth.get(idx[i]) or {}).get(th); prv = (breadth.get(idx[i-5]) or {}).get(th)
            brd[th] = (cur-prv) if (cur is not None and prv is not None) else None
        lead_ex = win("金融", i, i-4, i)     # 带动板块用"金融"篮子近似（券商/银行/保险）
        P[d] = {'r5': r5, 'accel': accel, 'r20': r20, 'brd': brd, 'lead': lead_ex}
    variants = ['A', 'B', 'C', 'D', 'E', 'F']
    res = {v: {'top1': [], 'top3': []} for v in variants}
    hit = {v: {1: [0, 0], 3: [0, 0]} for v in variants}
    for i in range(12, len(idx)):
        d = idx[i]
        if d not in P: continue
        p = P[d]
        za = MS.zscore({k: x for k, x in p['accel'].items() if x is not None})
        zr = MS.zscore({k: x for k, x in p['r5'].items() if x is not None})
        zb = MS.zscore({k: x for k, x in p['brd'].items() if x is not None})
        scores = {}
        for th in uni:
            a, r, b = za.get(th, 0.0), zr.get(th, 0.0), zb.get(th, 0.0)
            pos_high = (p['r20'].get(th) or 0) > 0.05
            pos_low = (p['r20'].get(th) or 0) < -0.03
            lead_weak = (p['lead'] is not None and p['lead'] < 0)
            s = {}
            s['A'] = a
            s['B'] = r
            s['C'] = a + r
            s['D'] = a + r + 0.5*b
            s['E'] = (1.5*r + a) if pos_high else ((1.5*a + r) if pos_low else (a + r))
            s['F'] = s['E'] - (0.5*max(0.0, r) if lead_weak else 0.0)
            scores[th] = s
        for v in variants:
            rank = sorted(uni, key=lambda th: -scores[th][v])
            for k in (1, 3):
                vals = []
                for th in rank[:k]:
                    f = fwd(d, th)[0]
                    if f is not None: vals.append(f)
                if vals: res[v]['top%d' % k] += vals
            if d in his:
                for k in (1, 3):
                    hit[v][k][1] += 1
                    if set(his[d]) & set(rank[:k]): hit[v][k][0] += 1
    print('| 方案 | top1 均值 | top1 t | top1 正比例 | top3 均值 | top3 t | 他方向落 top1 | 落 top3 |')
    print('|---|---|---|---|---|---|---|---|')
    names = {'A':'A 只按"在动"','B':'B 只按"有行情"','C':'C 在动+有行情','D':'D C+参与面扩散',
             'E':'E D+位置分层','F':'F E+带动板块闸'}
    out = {}
    for v in variants:
        s1, s3 = stat(res[v]['top1']), stat(res[v]['top3'])
        h1 = '%d/%d=%.0f%%' % (hit[v][1][0], hit[v][1][1], 100.0*hit[v][1][0]/max(1,hit[v][1][1]))
        h3 = '%d/%d=%.0f%%' % (hit[v][3][0], hit[v][3][1], 100.0*hit[v][3][0]/max(1,hit[v][3][1]))
        print('| %s | %s | %s | %s | %s | %s | %s | %s |' % (names[v], s1.get('mean'), s1.get('t'), s1.get('pos'),
              s3.get('mean'), s3.get('t'), h1, h3))
        out[v] = {'top1': s1, 'top3': s3, 'his_top1': h1, 'his_top3': h3}
    json.dump(out, open('/app/data/eval_ms_variants.json','w'), ensure_ascii=False, indent=1)
    db.close()

if __name__ == '__main__':
    main()

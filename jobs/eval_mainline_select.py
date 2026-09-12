# -*- coding: utf-8 -*-
"""eval_mainline_select.py — 方向层「选主线」的历史验收（PIT，2026-09-12）。

口径与之前一致，便于对比：**选题后 5 日超额**（主题等权复利 − 全市场等权复利）；
打分只用 ≤ D 的数据（PIT）；对比基线：① 全体主题均值 ② 现有 gate 确认集合（+0.41% 是他 1–7 月的水平）。
输出：top1/top3/top5 的均值与 t、按位置分档、月度、以及"他实际在做的方向是否落在我们的 top-N"。
用法：python jobs/eval_mainline_select.py [--topk 1,3,5] [--start 20260105] [--end 20260911]
"""
import json, os, sys, collections
import pandas as pd
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/backend')
from app.database import SessionLocal
from sqlalchemy import text
from app.services import wolf_mainline_select as MS


def main():
    argv = sys.argv
    start, end = '20260105', '20260911'
    if '--start' in argv: start = argv[argv.index('--start') + 1]
    if '--end' in argv: end = argv[argv.index('--end') + 1]
    db = SessionLocal()
    print('[eval] 载入行情…', flush=True)
    rows = db.execute(text("SELECT trade_date, ts_code, pct_chg FROM mkt_bars_daily "
                           "WHERE pct_chg IS NOT NULL")).all()
    df = pd.DataFrame(rows, columns=['d', 'ts', 'pc'])
    df['pc'] = df['pc'].astype(float) / 100.0
    wide = df.pivot_table(index='d', columns='ts', values='pc', aggfunc='first').sort_index()
    days = [d for d in wide.index.tolist() if start <= d <= end]
    px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in wide.index}
    uni, lead, allc = MS.load_universe()
    print('[eval] 主题 %d 个 | 带动板块 %d 只 | 全市场 %d 只 | 交易日 %d' % (len(uni), len(lead), len(allc), len(days)), flush=True)
    # 宽度：成分股站上 MA20 的比例
    ma20 = wide.rolling(20).mean()
    above = (wide > ma20)
    breadth = {}
    for d in days:
        if d not in above.index: continue
        row = above.loc[d]
        breadth[d] = {th: (sum(1 for c in uni[th] if bool(row.get(c, False))) / max(1, len(uni[th]))) for th in uni}
    # 他实际在做的方向（用于"是否落在我们 top-N"）
    dmap = {r['dir_text']: r['theme'] for r in db.execute(text("SELECT dir_text, theme FROM wolf_dir_theme_map")).mappings().all()}
    his = {}
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s: his[r['trade_date']] = s
    # gate 确认集合
    gate = {}
    for r in db.execute(text("SELECT trade_date, payload FROM daily_artifacts WHERE artifact_key='mainline_gate'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'])
        gate[r['trade_date']] = [x['theme'] for x in (p.get('rows') or []) if x.get('gate') and x.get('theme') in uni]
    # 基准（全市场等权）
    mkt = {d: (sum(px[d].values()) / len(px[d])) for d in days if px.get(d)}
    def fwd(d, th, h=5):
        i = wide.index.get_loc(d)
        seg = wide.index[i+1:i+1+h]
        if len(seg) < h or th not in uni: return None, None
        c = 1.0
        for dd in seg:
            v = [px[dd][x] for x in uni[th] if x in px.get(dd, {})]
            if v: c *= (1 + sum(v)/len(v))
        m = 1.0
        for dd in seg: m *= (1 + mkt.get(dd, 0.0))
        # 位置（过去20日主题涨幅）
        i0 = max(0, i-20)
        c0 = 1.0
        for dd in wide.index[i0:i]:
            v = [px[dd][x] for x in uni[th] if x in px.get(dd, {})]
            if v: c0 *= (1 + sum(v)/len(v))
        return (c - m) * 100, (c0 - 1) * 100
    def stat(v):
        n = len(v)
        if not n: return {'n': 0}
        m = sum(v)/n; sd = (sum((x-m)**2 for x in v)/max(1, n-1))**0.5
        return {'n': n, 'mean': round(m, 3), 't': round(m/(sd/n**0.5), 2) if sd else None,
                'pos': round(sum(1 for x in v if x > 0)/n, 3)}
    res = collections.defaultdict(list); base = []; mon = collections.defaultdict(list)
    hit_his_topN = collections.defaultdict(lambda: [0, 0]); pos_band = collections.defaultdict(list)
    n_days = 0
    for d in days:
        i = wide.index.get_loc(d)
        sc = MS.score_day(wide.index.tolist(), i, px, uni, lead, allc, breadth)
        if not sc: continue
        rank = sorted(sc.items(), key=lambda kv: -kv[1]['score'])
        fwds = {th: fwd(d, th) for th, _ in rank}
        ok = [(th, f) for th, f in fwds.items() if f[0] is not None]
        if len(ok) < 5: continue
        n_days += 1
        base += [f[0] for _, f in ok]
        mon[d[:6]] += [ok[0][1][0]] if False else []
        for k in (1, 3, 5):
            top = [th for th, _ in rank[:k]]
            vals = [fwds[th][0] for th in top if fwds[th][0] is not None]
            if vals: res['top%d' % k] += vals
        # 位置分档（用 top3 的方向）
        for th in [t for t, _ in rank[:3]]:
            f = fwds[th]
            if f[0] is None or f[1] is None: continue
            band = '低位(<-3%)' if f[1] < -3 else ('中位(-3~5%)' if f[1] <= 5 else '高位(>5%)')
            pos_band[band].append(f[0])
        # 他的方向是否在我们 topN
        if d in his:
            for k in (1, 3, 5):
                hit_his_topN[k][1] += 1
                if set(his[d]) & {t for t, _ in rank[:k]}: hit_his_topN[k][0] += 1
            # 他方向的表现（同口径）
            for th in his[d]:
                v = fwd(d, th)[0]
                if v is not None: mon[d[:6]] += [v]
    print('[eval] 评分天数 %d' % n_days, flush=True)
    print('[eval] 全市场主题均值（基线）:', stat(base), flush=True)
    for k in ('top1', 'top3', 'top5'):
        print('[eval] 我们 %-5s 后5日超额: %s' % (k, stat(res[k])), flush=True)
    print('[eval] 位置分档:', {b: stat(v) for b, v in pos_band.items()}, flush=True)
    print('[eval] 他的方向落在我们 topN 的比例:', {k: ('%d/%d=%.0f%%' % (v[0], v[1], 100.0*v[0]/max(1, v[1]))) for k, v in hit_his_topN.items()}, flush=True)
    print('[eval] 月度（他方向表现）:', {m: stat(v) for m, v in sorted(mon.items()) if v}, flush=True)
    out = {'n_days': n_days, 'baseline': stat(base),
           **{k: stat(res[k]) for k in ('top1', 'top3', 'top5')},
           'by_position': {b: stat(v) for b, v in pos_band.items()},
           'his_in_topN': {k: v for k, v in hit_his_topN.items()}}
    json.dump(out, open('/app/data/eval_mainline_select.json', 'w'), ensure_ascii=False, indent=1)
    print('[eval] 已写 /app/data/eval_mainline_select.json', flush=True)
    db.close()


if __name__ == '__main__':
    main()

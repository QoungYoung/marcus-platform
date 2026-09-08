# -*- coding: utf-8 -*-
"""trend_heat_calibrate.py — heat 因子权重 PIT 标定(2026-09-09)
判据: Wolf 标注行(expect True: rank<=2 match / False: rank>=3 match), 每行因子用截至当日数据(全14主题 percentile)
因子: mf5(主力5日)/rel(r20)/mf_accel; 权重网格 step0.1 归一。产物 trend_heat_params.json
"""
import sys, os, json, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    from trend_confirm import load_by_name
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEME_OF, MAIN_THEMES
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    by = load_by_name(os.path.join(DATA, 'concept_long.json'))
    days = sorted({d for v in by.values() for d in v.get('dates', [])})
    labels = json.load(open(os.path.join(DATA, 'wolf_labels_v2.json'), encoding='utf-8'))['mainline']
    rows = []
    for l in labels:
        mt = MAIN_THEME_OF.get(l.get('theme'))
        if mt is None or mt not in THEME_CONCEPTS: continue
        d8 = str(l['date']).replace('-', '')
        if d8 not in days: continue
        if days.index(d8) + 1 < 120: continue
        rows.append({'date': d8, 'theme': mt, 'expect': bool(l.get('expect'))})
    print('usable labels', len(rows), 'True', sum(1 for r in rows if r['expect']), flush=True)
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    th_members = {}
    for th, cons in THEME_CONCEPTS.items():
        ts = []
        for c in cons:
            for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,)):
                ts.append(r[0])
        th_members[th] = list(dict.fromkeys(ts))
    themes = [th for th in MAIN_THEMES if th in THEME_CONCEPTS]
    need = set()
    for r in rows:
        i = days.index(r['date'])
        need.update(days[max(0, i - 9):i + 1])
    need = sorted(need)
    print('moneyflow days need', len(need), need[0], '-', need[-1], flush=True)
    mf = {}
    for d in need:
        try:
            df = pro.moneyflow_dc(trade_date=d, fields='ts_code,net_amount')
            if df is not None and not df.empty:
                mf[d] = {str(rr['ts_code']): float(rr['net_amount']) for _, rr in df.iterrows()}
        except Exception:
            pass
        time.sleep(0.08)
    print('moneyflow got', len(mf), flush=True)
    row_fac = {}
    for r in rows:
        d8 = r['date']
        i = days.index(d8)
        w = days[max(0, i - 9):i + 1]
        vals = {}
        for th in themes:
            mem = th_members.get(th) or []
            s5 = sum(mf[x].get(t, 0.0) for x in w[-5:] for t in mem)
            s10 = sum(mf[x].get(t, 0.0) for x in w for t in mem)
            acc = (s5 / 5.0) - ((s10 - s5) / 5.0)
            pcts = []
            for c in THEME_CONCEPTS[th]:
                v = by.get(c)
                if not v: continue
                ds = v['dates']; cl = v['close']
                n = sum(1 for x in ds if x <= d8)
                if n < 25: continue
                cc = cl[:n]; base = cc[0]
                if base <= 0: continue
                pcts.append([(x / base - 1.0) * 100.0 for x in cc])
            r20 = 0.0
            if pcts:
                m = len(pcts[0])
                idx = [sum(p[j] for p in pcts) / len(pcts) for j in range(m)]
                r20 = (idx[-1] / idx[-21] - 1.0) if len(idx) > 21 else 0.0
            vals[th] = (s5, acc, r20)
        row_fac[(d8, r['theme'])] = vals
    print('row factors built', len(row_fac), flush=True)
    combos = []
    for a in range(1, 10):
        for b in range(1, 10 - a):
            combos.append((a / 10.0, b / 10.0, (10 - a - b) / 10.0))
    def rank_pos(w3, d8, th):
        vals = row_fac[(d8, th)]
        sc = {}
        for t in themes:
            s5, acc, r20 = vals[t]
            mf5p = sum(1 for x in themes if vals[x][0] <= s5) / len(themes)
            accp = sum(1 for x in themes if vals[x][1] <= acc) / len(themes)
            relp = sum(1 for x in themes if vals[x][2] <= r20) / len(themes)
            sc[t] = w3[0] * mf5p + w3[1] * relp + w3[2] * accp
        return sorted(themes, key=lambda t: -sc[t]).index(th) + 1
    results = []
    for w3 in combos:
        ok = 0
        for r in rows:
            pos = rank_pos(w3, r['date'], r['theme'])
            m = (pos <= 2) if r['expect'] else (pos >= 3)
            if m: ok += 1
        results.append((round(ok / len(rows), 3), w3))
    best = max(results, key=lambda x: x[0])
    pl = [x for x in results if x[0] >= best[0] - 0.02]
    print('best match', best, 'plateau', len(pl), flush=True)
    for x in sorted(results, key=lambda y: -y[0])[:6]: print(x, flush=True)
    json.dump({'best': best, 'plateau': pl, 'labels': len(rows)},
              open(os.path.join(DATA, 'trend_heat_params.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE /app/data/trend_heat_params.json')

if __name__ == '__main__':
    main()

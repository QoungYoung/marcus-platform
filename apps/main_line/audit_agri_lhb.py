# -*- coding: utf-8 -*-
"""audit_agri_lhb.py — 农业机构龙虎榜 20日 -3.4亿 分歧复核(2026-09-09)
按日拆机构席位净买时序 + 逐股, 对照农业主题指数日涨跌, 判定: 一次性兑现 vs 持续撤出
产物: /app/data/agri_lhb_audit.json
"""
import sys, os, json, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    from trend_confirm import load_by_name
    from fusion_mainline import THEME_CONCEPTS
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    by = load_by_name(os.path.join(DATA, 'concept_long.json'))
    days = sorted({d for v in by.values() for d in v.get('dates', []) if '20260720' <= d <= '20260908'})
    w20 = [d for d in days if d >= '20260810']
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    mem = set()
    for c in THEME_CONCEPTS['农业']:
        for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,)):
            mem.add(str(r[0]))
    # 主题指数(农业, 用于对照日涨跌)
    pcts = []
    for c in THEME_CONCEPTS['农业']:
        v = by.get(c)
        if not v: continue
        cl = v['close']; base = cl[0]
        if base <= 0: continue
        pcts.append([(x / base - 1.0) * 100.0 for x in cl])
    dsl = next(iter(by.values()))['dates']
    idx = []
    if pcts:
        m = len(pcts[0])
        idx = [sum(p[j] for p in pcts) / len(pcts) for j in range(m)]
    d2v = {d: (idx[i] if i < len(idx) else None) for i, d in enumerate(dsl)}
    rows = []
    for d in w20:
        try:
            df = pro.top_inst(trade_date=d)
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    ex = str(r.get('exalter') or '')
                    rows.append({'d': d, 'ts': str(r['ts_code']), 'inst': '机构' in ex,
                                 'net': float(r.get('net_buy') or 0)})
        except Exception as e:
            print('err', d, str(e)[:60])
        time.sleep(0.1)
    hit = [r for r in rows if r['ts'] in mem and r['inst']]
    print('top_inst rows', len(rows), '农业机构命中', len(hit), flush=True)
    # 按日
    by_day = {}
    for r in hit: by_day.setdefault(r['d'], []).append(r)
    print('%-10s %12s %10s %8s' % ('日期', '机构净买(万)', '农业指数日%', '上榜股数'), flush=True)
    day_rows = []
    for d in sorted(by_day):
        s = sum(r['net'] for r in by_day[d])
        pv = d2v.get(d)
        pi = dsl.index(d) - 1
        prev = d2v.get(dsl[pi]) if pi >= 0 else None
        chg = ((100.0 + pv) / (100.0 + prev) - 1.0) if (pv is not None and prev is not None) else None
        day_rows.append({'date': d, 'inst_net': round(s, 0), 'idx_chg_pct': chg and round(chg * 100, 2), 'n': len(by_day[d])})
        print('%-10s %+12.0f %10s %8d' % (d, s / 1e4, ('%+.2f%%' % (chg * 100)) if chg is not None else '-', len(by_day[d])), flush=True)
    # 按股
    by_ts = {}
    for r in hit: by_ts.setdefault(r['ts'], 0.0); by_ts[r['ts']] += r['net']
    stocks = sorted(by_ts.items(), key=lambda kv: kv[1])
    print('按股(净卖前5 / 净买前5):', flush=True)
    for ts, v in stocks[:5]: print('  卖出', ts, '%+.1f万' % (v / 1e4), flush=True)
    for ts, v in stocks[-5:]: print('  买入', ts, '%+.1f万' % (v / 1e4), flush=True)
    total = sum(by_ts.values())
    json.dump({'window': [w20[0], w20[-1]], 'total_inst_net': round(total, 0), 'by_day': day_rows,
               'by_ts': [{'ts': t, 'net': round(v, 0)} for t, v in stocks]},
              open(os.path.join(DATA, 'agri_lhb_audit.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('TOTAL 农业机构20日净买: %+.1f万' % (total / 1e4), flush=True)
    print('WROTE /app/data/agri_lhb_audit.json')

if __name__ == '__main__':
    main()

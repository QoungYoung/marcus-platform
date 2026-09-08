# -*- coding: utf-8 -*-
"""build_concept_long.py — concept_long 长历史概念收盘指数(2026-09-08)
源: gzcloud 全市场 daily(trade_date 逐日批量, 实测 20240102 起 5300+行; 区间批量受~6000行/次上限,
     逐日全市场为调用数最少的批量形态, 每次覆盖全部成分成员)。
方法: THEME_CONCEPTS 104概念 x stock_pool.db stock_concept_map 全量成分(4676只去重)
      -> 等权 pct 环比累计合成概念收盘指数(规避未复权除权跳变; 停牌沿用昨收涨跌0)。
产物: /app/data/concept_long.json {meta, dates:[交易日], series:{concept:[100,...]}}  起点20250101
用法: python3 build_concept_long.py [YYYYMMDD起始,默认20250101]
"""
import sys, os, json, sqlite3, time, requests, urllib3
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
urllib3.disable_warnings()
DATA = os.environ.get('DATA_DIR', '/app/data')
GZ = 'https://ts.gyzcloud.top/api'; TOK = os.getenv('TUSHARE_TOKEN', 'a5c495cbe5e14729ad756381efe1fd72')

def gz(params, fields='ts_code,close'):
    for _ in range(3):
        try:
            r = requests.post(GZ, json={'api_name': 'daily', 'token': TOK, 'params': params, 'fields': fields}, timeout=60)
            it = ((r.json().get('data') or {}).get('items') or [])
            if it: return it
        except Exception:
            pass
        time.sleep(1.0)
    return []

def cal(s, e):
    r = requests.post(GZ, json={'api_name': 'trade_cal', 'token': TOK,
                                'params': {'exchange': 'SSE', 'start_date': s, 'end_date': e},
                                'fields': 'cal_date,is_open'}, timeout=30)
    return sorted(x[0] for x in ((r.json().get('data') or {}).get('items') or []) if x[1] == 1)

def main():
    argv = sys.argv[1:]
    seed_mode = '--seed' in argv
    start = argv[0] if argv and not argv[0].startswith('-') else '20250101'
    end = time.strftime('%Y%m%d')
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    members = {}   # concept -> [ts]
    ts_conc = {}   # ts -> [concept]
    if seed_mode:
        sys.path.insert(0, '/app/apps/main_line')
        import chain_map as cm
        src = {}
        for t, segs in cm.SEED.items():
            for s in segs:
                for c in s.get('concepts', []):
                    src.setdefault(c, True)
    else:
        from fusion_mainline import THEME_CONCEPTS
        src = {c: True for cons in THEME_CONCEPTS.values() for c in cons}
    for c in src:
        rows = [r[0] for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,))]
        rows = list(dict.fromkeys(rows))
        members[c] = rows
        for ts in rows: ts_conc.setdefault(ts, []).append(c)
    all_ts = list(ts_conc)
    print('concepts', len(members), 'members', len(all_ts), flush=True)
    opens = [d for d in cal(start, end) if d >= start]
    print('days', len(opens), start, '->', end, flush=True)
    last = {}                     # ts -> prev close
    last_cnt = {}                 # ts 首次出现前不可算
    cum = {c: [] for c in members}
    first_done = {c: False for c in members}
    t0 = time.time()
    for di, d in enumerate(opens):
        rows = gz({'trade_date': d})
        px = {}
        for x in rows:
            try: px[x[0]] = float(x[1])
            except Exception: pass
        for c, mts in members.items():
            tot = 0.0; n = 0
            for ts in mts:
                prev = last.get(ts)
                c_now = px.get(ts)
                if prev is not None:
                    if c_now is not None:
                        tot += (c_now / prev - 1.0)
                    n += 1
            if n > 0:
                pct = tot / n
                if not first_done[c]:
                    cum[c].append(100.0); first_done[c] = True
                cum[c].append(cum[c][-1] * (1.0 + pct))
            elif first_done[c]:
                cum[c].append(cum[c][-1])  # 全员停牌沿用
        for ts, c in px.items():
            if ts in ts_conc: last[ts] = c
        if (di + 1) % 50 == 0 or di == len(opens) - 1:
            print('day', di + 1, '/', len(opens), d, '%.0fs' % (time.time() - t0), flush=True)
    series = {}
    for c in members:
        # 与 opens 对齐(首个有效日起)
        off = len(opens) - len(cum[c])
        dates = opens[off:] if off > 0 else opens
        series[c] = {'dates': dates, 'close': cum[c]}
    out = {'meta': {'generator': 'build_concept_long_v1' + ('_seed' if seed_mode else ''), 'start': start, 'end': end,
                    'source': 'gzcloud daily 全市场等权pct累计', 'concepts': len(series),
                    'members': len(all_ts), 'note': '未复权已用pct环比规避; 停牌沿用昨收'},
           'series': series}
    p = os.path.join(DATA, 'concept_long_seed.json' if seed_mode else 'concept_long.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    print('WROTE', p, 'size', os.path.getsize(p) // 1024, 'KB, %.0fs' % (time.time() - t0), flush=True)

if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""eval_pool_k.py — 一次加载、循环多个池宽 K（回答"池宽是否限制了对他方向的覆盖"）。

背景：T6 池 = 量能占比 topK ∩ r5>0，K=3 时 recall 只有 55%、26% 的日子"完全不在池内"。
他的次要方向（资源 39 天、军工 19 天）**量能占比天然排不进 top3**（资源约 17% vs 前三约 30%+）
→ 池宽 K 可能是覆盖不足的一个结构性原因。本脚本测 K=3/4/5/6 的 recall / precision / top1 / 收益。

为省时间：把 PG 聚合结果缓存到本地 pickle，重复跑不重新查库。
用法：python jobs/eval_pool_k.py --start 20260105 --end 20260911 [--ks 3,4,5,6] [--refresh]
"""
import collections
import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.dsh-tmp/wolfbt')
from eval_pool_alignment import KW          # noqa: E402
from local_pg import ensure_tunnel, DSN      # noqa: E402
ensure_tunnel()

CACHE = '.dsh-tmp/wolfbt/align_cache.pkl'


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def load(start, end, refresh=False):
    if os.path.exists(CACHE) and not refresh:
        return pd.read_pickle(CACHE)
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE _k_memb (ts_code varchar(16), theme varchar(32))")
    rows = []
    for th, kws in KW.items():
        for kw in kws:
            cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE %s",
                        ('%' + kw + '%',))
            rows += [(ts, th) for (ts,) in cur.fetchall() if ts]
    cur.executemany("INSERT INTO _k_memb VALUES (%s,%s)", list(set(rows)))
    cur.execute("""SELECT b.trade_date, m.theme, avg(b.pct_chg)::float8, sum(b.amount)::float8
                   FROM mkt_bars_daily b JOIN _k_memb m ON m.ts_code=b.ts_code
                   WHERE b.trade_date >= %s AND b.trade_date <= %s AND b.pct_chg IS NOT NULL
                   GROUP BY 1,2""", (start, end))
    g = pd.DataFrame(cur.fetchall(), columns=['d', 'th', 'pc', 'amt'])
    cur.execute("""SELECT trade_date, avg(pct_chg)::float8, sum(amount)::float8 FROM mkt_bars_daily
                   WHERE trade_date >= %s AND trade_date <= %s AND pct_chg IS NOT NULL GROUP BY 1""",
                (start, end))
    m = pd.DataFrame(cur.fetchall(), columns=['d', 'pc', 'amt'])
    cur.execute("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")
    hr = cur.fetchall()
    cur.execute("SELECT dir_text, theme FROM wolf_dir_theme_map")
    dmap = dict(cur.fetchall())
    conn.close()
    data = {'g': g, 'm': m, 'his_raw': hr, 'dmap': dmap}
    pd.to_pickle(data, CACHE)
    return data


def main():
    start = sys.argv[sys.argv.index('--start') + 1] if '--start' in sys.argv else '20260105'
    end = sys.argv[sys.argv.index('--end') + 1] if '--end' in sys.argv else '20260911'
    KS = [int(x) for x in sys.argv[sys.argv.index('--ks') + 1].split(',')] if '--ks' in sys.argv else [3, 4, 5, 6]
    d = load(start, end, '--refresh' in sys.argv)
    g, m, dmap = d['g'], d['m'], d['dmap']
    pc = g.pivot_table(index='d', columns='th', values='pc', aggfunc='first').sort_index().astype(float) / 100.0
    amt = g.pivot_table(index='d', columns='th', values='amt', aggfunc='first').reindex(pc.index).astype(float)
    mk = m.set_index('d')['pc'].astype(float).reindex(pc.index) / 100.0
    mamt = m.set_index('d')['amt'].astype(float).reindex(pc.index)
    idx = pc.index.tolist()
    r5 = (1 + pc).rolling(5).apply(np.prod, raw=True).sub((1 + mk).rolling(5).apply(np.prod, raw=True), axis=0)
    share5 = amt.rolling(5).sum().div(mamt.rolling(5).sum(), axis=0)
    f5 = (1 + pc).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = (f5.sub(f5m, axis=0)) * 100
    his = {}
    for dd, payload in d['his_raw']:
        p = payload if isinstance(payload, dict) else json.loads(payload or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s:
            his[dd] = s
    print('[K] %s→%s 交易日 %d | 有他方向 %d 天' % (start, end, len(idx), len(his)), flush=True)
    print('| 池宽 K | n | recall | precision | 全部在池内 | 完全不在池内 | top1命中 | top3命中 | top1后5日超额 | t | 胜率 |', flush=True)
    print('|---|---|---|---|---|---|---|---|---|---|---|', flush=True)
    rows = []
    for K in KS:
        rec, prec, h1, h3, allin, nonein, rets = [], [], [], [], 0, 0, []
        for i in range(20, len(idx) - 5):
            dd = idx[i]
            hs = set(his.get(dd) or [])
            if not hs:
                continue
            r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
            sh = {t: v for t, v in share5.iloc[i].items() if v == v}
            if len(r5d) < 4 or len(sh) < 5:
                continue
            topK = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:K]}
            pool = {t for t in topK if (r5d.get(t) or 0) > 0}
            if not pool:
                continue
            rec.append(len(pool & hs) / len(hs))
            prec.append(len(pool & hs) / len(pool))
            ranked = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in pool]
            h1.append(1 if (ranked and ranked[0] in hs) else 0)
            h3.append(1 if set(ranked[:3]) & hs else 0)
            if ranked:
                fv = fwd5.iloc[i].get(ranked[0])
                if fv is not None and fv == fv:
                    rets.append(float(fv))
            allin += 1 if hs <= pool else 0
            nonein += 1 if not (hs & pool) else 0
        n = max(1, len(rec))
        s = stat(rets)
        rows.append({'K': K, 'n': n, 'recall': round(sum(rec) / n, 3), 'precision': round(sum(prec) / n, 3),
                     'all_in': round(allin / n, 3), 'none_in': round(nonein / n, 3),
                     'top1': round(sum(h1) / max(1, len(h1)), 3), 'top3': round(sum(h3) / max(1, len(h3)), 3),
                     'ret': s})
        print('| %d | %d | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %s | %s | %s |'
              % (K, n, 100 * sum(rec) / n, 100 * sum(prec) / n, 100 * allin / n, 100 * nonein / n,
                 100 * sum(h1) / max(1, len(h1)), 100 * sum(h3) / max(1, len(h3)),
                 s.get('mean'), s.get('t'), s.get('pos')), flush=True)
    json.dump(rows, open('data/eval_pool_k.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[K] → data/eval_pool_k.json', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

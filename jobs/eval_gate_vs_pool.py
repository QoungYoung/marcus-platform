# -*- coding: utf-8 -*-
"""eval_gate_vs_pool.py — gate 到底该不该留：`池` vs `池∩gate` vs `gate 单独` 三方对照。

用户质疑（2026-09-13）：「gate 跟狼大的主线不一致，为什么不直接去掉？」
已知：①gate 单独命中他的方向 ≈ 随机（75% vs 77%，因为每天确认 10/13 个主题）；②但在他池的变体里，
      `P10(∩gate)` H2 +2.44% vs `P15(不∩gate)` H2 −0.02% → gate 在 2026 H2 似乎有过滤价值。
本脚本用**同一个量能池（T6）**做三方对照（同期间、同口径），回答"资格闸是否还有必要"。

口径：池 = 主题近5日成交额占比 top3 ∩ 近5日相对强度>0；gate = daily_artifacts.mainline_gate.rows[].gate。
度量：他方向命中（top1/top3）、recall/precision、后5日超额、H1/H2。数据：本地缓存 + PG(隧道) 读 gate。
"""
import collections
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.dsh-tmp/wolfbt')
from eval_pool_alignment import KW, DSN       # noqa: E402
from local_pg import ensure_tunnel            # noqa: E402

CACHE = '.dsh-tmp/wolfbt/align_cache.pkl'
OUT = 'data/eval_gate_vs_pool.json'


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def main():
    ensure_tunnel()
    import psycopg2
    d = pd.read_pickle(CACHE)
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

    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("SELECT trade_date, payload FROM daily_artifacts WHERE artifact_key='mainline_gate'")
    gates = {}
    for dd, p in cur.fetchall():
        o = p if isinstance(p, dict) else json.loads(p or '{}')
        gates[dd] = [x['theme'] for x in (o.get('rows') or []) if x.get('gate')]
    cur.execute("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")
    hr = cur.fetchall()
    conn.close()
    kind = json.load(open('data/wolf_dir_kind.json', encoding='utf-8'))
    EXCL = ('资源/周期', '军工/航天')
    his = {}
    for dd, payload in hr:
        o = payload if isinstance(payload, dict) else json.loads(payload or '{}')
        doing = [(x.get('dir') or '').strip() for x in (o.get('doing') or [])]
        s = sorted({dmap.get(x) for x in doing if (kind.get(x) or {}).get('kind') == 'stock_direction'} - {None, '非主题'} - set(EXCL))
        if s:
            his[dd] = s
    print('[gvp] 交易日 %d | gate 日 %d | 他(股票主线层) 日 %d' % (len(idx), len(gates), len(his)), flush=True)

    variants = {
        'A_池T6(不用gate)': lambda pool, gs: pool,
        'B_池T6∩gate': lambda pool, gs: ([t for t in pool if t in gs] or pool),
        'C_gate单独(旧判定)': lambda pool, gs: (gs or pool),
    }
    rows = []
    print('\n| 变体 | n天 | top1命中 | top3命中 | recall | precision | 完全不在池内 | 后5日超额 | t | 胜率 | H1 | H2 |',
          flush=True)
    print('|---|---|---|---|---|---|---|---|---|---|---|---|', flush=True)
    mid = idx[len(idx) // 2]
    for name, fn in variants.items():
        h1, h3, rec, prec, rets, nonein = [], [], [], [], [], 0
        for i in range(20, len(idx) - 5):
            dd = idx[i]
            hs = set(his.get(dd) or [])
            r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
            sh = {t: v for t, v in share5.iloc[i].items() if v == v}
            if len(r5d) < 4 or len(sh) < 5 or not hs:
                continue
            top3 = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:3]}
            pool = {t for t in top3 if (r5d.get(t) or 0) > 0}
            if not pool:
                continue
            gs = set(gates.get(dd) or [])
            cand = set(fn(pool, gs))
            ranked = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in cand]
            if not ranked:
                continue
            h1.append(1 if ranked[0] in hs else 0)
            h3.append(1 if set(ranked[:3]) & hs else 0)
            rec.append(len(cand & hs) / len(hs))
            prec.append(len(cand & hs) / len(cand))
            nonein += 1 if not (cand & hs) else 0
            fv = fwd5.iloc[i].get(ranked[0])
            if fv is not None and fv == fv:
                rets.append((dd, float(fv)))
        if not h1:
            continue
        s = stat([x[1] for x in rets])
        hh1 = stat([x[1] for x in rets if x[0] <= mid])
        hh2 = stat([x[1] for x in rets if x[0] > mid])
        n = len(h1)
        rows.append({'variant': name, 'n': n, 'top1': round(sum(h1) / n, 3), 'top3': round(sum(h3) / n, 3),
                     'recall': round(sum(rec) / n, 3), 'precision': round(sum(prec) / n, 3),
                     'none_in': round(nonein / n, 3), 'ret': s, 'H1': hh1, 'H2': hh2})
        print('| %s | %d | **%.0f%%** | **%.0f%%** | %.0f%% | %.0f%% | %.0f%% | %s | %s | %s | %s | %s |'
              % (name, n, 100 * sum(h1) / n, 100 * sum(h3) / n, 100 * sum(rec) / n, 100 * sum(prec) / n,
                 100 * nonein / n, s.get('mean'), s.get('t'), s.get('pos'), hh1.get('mean'), hh2.get('mean')),
              flush=True)
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[gvp] → %s' % OUT, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

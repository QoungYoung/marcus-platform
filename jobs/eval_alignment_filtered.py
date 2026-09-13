# -*- coding: utf-8 -*-
"""eval_alignment_filtered.py — 上线前验收基线：**三种"他"的口径**下我们池的对齐度。

为什么：`wolf_actual_mainline.doing` 里混着**期货商品仓**（"油（多油）""黄金期货"）、**非方向词**（"整体仓位""ETF"）
与**操作模式词**（"低位方向""打野""做T"）。拿我们**股票方向层**的池去比一个含期货/模式的全集，会结构性压低 recall。

三种口径（逐层收紧，全部有语料依据）：
  · **F0 原口径**：他的全部 doing（445 条）
  · **F1 股票方向层**：只保留 `stock_direction` 标签（用 `jobs/classify_dir_labels.py` 经 dsh 分类，232 个标签）
  · **F2 股票主线层**：F1 再去掉 **资源/周期** 与 **军工/航天**——依据 `wolf_why_res_def.json` 的角色统计：
      军工「主线 0 / 低位埋伏 21 / 超短打短 17」；资源「主线 8 但"股票我只做油 期货做金油"」（油/金走期货体系）。

池 = 主题近 5 日成交额占比 top3 ∩ 近 5 日相对强度>0；选中 = 池内 r5 top1；度量 = 后 5 日超额（T+1 起算）。
"""
import collections
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.dsh-tmp/wolfbt')
from eval_pool_alignment import KW, DSN          # noqa: E402
from local_pg import ensure_tunnel               # noqa: E402

CACHE = '.dsh-tmp/wolfbt/align_cache.pkl'
KIND = 'data/wolf_dir_kind.json'
OUT = 'data/eval_alignment_filtered.json'
EXCLUDE_F2 = ('资源/周期', '军工/航天')


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def load_data(start, end):
    if os.path.exists(CACHE):
        return pd.read_pickle(CACHE)
    import psycopg2
    ensure_tunnel()
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE _af_memb (ts_code varchar(16), theme varchar(32))")
    rows = []
    for th, kws in KW.items():
        for kw in kws:
            cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE %s", ('%' + kw + '%',))
            rows += [(ts, th) for (ts,) in cur.fetchall() if ts]
    cur.executemany("INSERT INTO _af_memb VALUES (%s,%s)", list(set(rows)))
    cur.execute("""SELECT b.trade_date, m.theme, avg(b.pct_chg)::float8, sum(b.amount)::float8
                   FROM mkt_bars_daily b JOIN _af_memb m ON m.ts_code=b.ts_code
                   WHERE b.trade_date >= %s AND b.trade_date <= %s AND b.pct_chg IS NOT NULL GROUP BY 1,2""",
                (start, end))
    g = pd.DataFrame(cur.fetchall(), columns=['d', 'th', 'pc', 'amt'])
    cur.execute("""SELECT trade_date, avg(pct_chg)::float8, sum(amount)::float8 FROM mkt_bars_daily
                   WHERE trade_date >= %s AND trade_date <= %s AND pct_chg IS NOT NULL GROUP BY 1""", (start, end))
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
    d = load_data(start, end)
    kind = json.load(open(KIND, encoding='utf-8'))
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

    # 三种口径下的"他"
    his = {'F0_原口径': {}, 'F1_股票方向层': {}, 'F2_股票主线层': {}}
    for dd, payload in d['his_raw']:
        p = payload if isinstance(payload, dict) else json.loads(payload or '{}')
        doing = [(x.get('dir') or '').strip() for x in (p.get('doing') or [])]
        doing = [x for x in doing if x]
        s0 = sorted({dmap.get(x) for x in doing} - {None, '非主题'})
        s1 = sorted({dmap.get(x) for x in doing
                     if (kind.get(x) or {}).get('kind') == 'stock_direction'} - {None, '非主题'})
        s2 = sorted(set(s1) - set(EXCLUDE_F2))
        if s0:
            his['F0_原口径'][dd] = s0
        if s1:
            his['F1_股票方向层'][dd] = s1
        if s2:
            his['F2_股票主线层'][dd] = s2
    for k, v in his.items():
        tot = sum(len(x) for x in v.values())
        print('[align] %-14s 有方向的天数 %3d | 方向条目 %d' % (k, len(v), tot), flush=True)

    rows = []
    print('\n| 口径 | n天 | recall | precision | 全部在池内 | 完全不在池内 | top1命中 | top3命中 | top1后5日超额 | t | 胜率 |',
          flush=True)
    print('|---|---|---|---|---|---|---|---|---|---|---|', flush=True)
    for label, hs_map in his.items():
        rec, prec, h1, h3, allin, nonein, rets, picks = [], [], [], [], 0, 0, [], collections.Counter()
        for i in range(20, len(idx) - 5):
            dd = idx[i]
            hs = set(hs_map.get(dd) or [])
            if not hs:
                continue
            r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
            sh = {t: v for t, v in share5.iloc[i].items() if v == v}
            if len(r5d) < 4 or len(sh) < 5:
                continue
            top3 = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:3]}
            pool = {t for t in top3 if (r5d.get(t) or 0) > 0}
            if not pool:
                continue
            rec.append(len(pool & hs) / len(hs))
            prec.append(len(pool & hs) / len(pool))
            ranked = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in pool]
            h1.append(1 if (ranked and ranked[0] in hs) else 0)
            h3.append(1 if set(ranked[:3]) & hs else 0)
            if ranked:
                picks[ranked[0]] += 1
                fv = fwd5.iloc[i].get(ranked[0])
                if fv is not None and fv == fv:
                    rets.append(float(fv))
            allin += 1 if hs <= pool else 0
            nonein += 1 if not (hs & pool) else 0
        n = max(1, len(rec))
        s = stat(rets)
        rows.append({'filter': label, 'n': n, 'recall': round(sum(rec) / n, 3), 'precision': round(sum(prec) / n, 3),
                     'all_in': round(allin / n, 3), 'none_in': round(nonein / n, 3),
                     'top1': round(sum(h1) / max(1, len(h1)), 3), 'top3': round(sum(h3) / max(1, len(h3)), 3),
                     'ret': s, 'picks': dict(picks)})
        print('| **%s** | %d | **%.0f%%** | %.0f%% | %.0f%% | %.0f%% | **%.0f%%** | **%.0f%%** | %s | %s | %s |'
              % (label, n, 100 * sum(rec) / n, 100 * sum(prec) / n, 100 * allin / n, 100 * nonein / n,
                 100 * sum(h1) / max(1, len(h1)), 100 * sum(h3) / max(1, len(h3)), s.get('mean'), s.get('t'),
                 s.get('pos')), flush=True)
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[align] → %s' % OUT, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

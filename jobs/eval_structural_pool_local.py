# -*- coding: utf-8 -*-
"""eval_structural_pool_local.py — 本地跑（SQLite + pandas）的结构池跨期验证，不碰生产容器。

为什么有本地版：重任务一律本地跑（容器 512MB cgroup 曾被撑爆致生产重启）。
数据：`data/mkt_bars_local.db`（promax 回填的行情）+ `data/stock_pool_local.db`（概念库，经 fetch 取回）
     + `/app/data/wolf_period_directions.json` 的本地副本（他的各期方向池）。

口径：
  · 主题 = 13 主题（概念库口径，复用 `wolf_mainline_select.THEME_CONCEPT_KW`）
  · `share5` = 主题近 5 日成交额 / 全市场近 5 日成交额；`r5` / `r20` = 近 5/20 日等权相对强度
  · 池 = `share5 top3`（T6 额外要求 `r5 > 0`）；选中 = 池内 r5 top1；度量 = 后 5 日超额（T+1 起算）
用法：PYTHONPATH=backend:. DATA_DIR=data WOLF_MS_UNIVERSE_DB=data/stock_pool_local.db \
      .venv/bin/python jobs/eval_structural_pool_local.py --start 20250101 --end 20251231
"""
import collections
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
from app.services import wolf_mainline_select as MS      # noqa: E402

BARS = os.getenv('LOCAL_BARS_DB', 'data/mkt_bars_local.db')
HIS_POOL = os.getenv('WOLF_PERIOD_DIRECTIONS', '.dsh-tmp/wolfbt/pd.json')


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def main():
    a = sys.argv[sys.argv.index('--start') + 1] if '--start' in sys.argv else '20250101'
    b = sys.argv[sys.argv.index('--end') + 1] if '--end' in sys.argv else '20251231'
    uni, _, _ = MS.load_universe()
    print('[local] 主题 %d 个：%s' % (len(uni), '、'.join(list(uni)[:6]) + '…'), flush=True)
    c = sqlite3.connect(BARS)
    df = pd.read_sql_query(
        "SELECT trade_date AS d, ts_code AS ts, pct_chg AS pc, amount AS amt FROM mkt_bars_daily "
        "WHERE trade_date >= ? AND trade_date <= ? AND pct_chg IS NOT NULL", c, params=(a, b))
    c.close()
    print('[local] 行情 %d 行 | %d 天 %s…%s' % (len(df), df['d'].nunique(), df['d'].min(), df['d'].max()),
          flush=True)
    df['pc'] = pd.to_numeric(df['pc'], errors='coerce') / 100.0
    df['amt'] = pd.to_numeric(df['amt'], errors='coerce')
    wide = df.pivot_table(index='d', columns='ts', values='pc', aggfunc='first').sort_index()
    amt = df.pivot_table(index='d', columns='ts', values='amt', aggfunc='sum').reindex(wide.index)
    idx = wide.index.tolist()
    mk = wide.mean(axis=1)                       # 全市场等权
    mamt = amt.sum(axis=1)
    cols = {th: [x for x in codes if x in wide.columns] for th, codes in uni.items()}
    pc = pd.DataFrame({th: wide[cs].mean(axis=1) for th, cs in cols.items() if cs}).reindex(idx)
    amt_t = pd.DataFrame({th: amt[cs].sum(axis=1) for th, cs in cols.items() if cs}).reindex(idx)
    r5 = (1 + pc).rolling(5).apply(np.prod, raw=True).sub((1 + mk).rolling(5).apply(np.prod, raw=True), axis=0)
    r20 = (1 + pc).rolling(20).apply(np.prod, raw=True).sub((1 + mk).rolling(20).apply(np.prod, raw=True), axis=0)
    share5 = amt_t.rolling(5).sum().div(mamt.rolling(5).sum(), axis=0)
    f5 = (1 + pc).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = (f5.sub(f5m, axis=0)) * 100
    print('[local] 主题日行 %d × %d | 有效交易日 %d' % (pc.shape[0], pc.shape[1], len(idx)), flush=True)

    # 他的各期方向池（跨期取证）
    his_pool = {}
    try:
        d = json.load(open(HIS_POOL, encoding='utf-8'))
        for per, obj in (d.get('periods') or {}).items():
            cnt = collections.Counter()
            for it in (obj.get('directions') or []):
                nm = (it.get('name') or '').strip()
                for th in uni:
                    if nm and (nm in th or th.split('/')[0] in nm):
                        cnt[th] += int(it.get('count') or 1)
                        break
            his_pool[per] = [t for t, _ in cnt.most_common(5)]
    except Exception as e:
        print('[local] 读他的方向池失败 %s' % str(e)[:60], flush=True)

    print('\n【① 结构池（量能占比 top3）月度构成】vs 他该季度实际方向池', flush=True)
    print('| 月份 | 结构池 top3 | 他该季度的池 top5 |', flush=True)
    print('|---|---|---|', flush=True)
    monthly = collections.defaultdict(collections.Counter)
    for i in range(20, len(idx)):
        row = {t: v for t, v in share5.iloc[i].items() if v == v}
        if len(row) < 5:
            continue
        for t, _ in sorted(row.items(), key=lambda kv: -kv[1])[:3]:
            monthly[idx[i][:6]][t] += 1
    for mo in sorted(monthly):
        q = '%sQ%d' % (mo[:4], (int(mo[4:]) - 1) // 3 + 1)
        print('| %s | %s | %s |' % (mo, '、'.join('%s(%d)' % (t, n) for t, n in monthly[mo].most_common(3)),
                                    '、'.join(his_pool.get(q) or []) or '—'), flush=True)

    print('\n【② 结构池表现（后 5 日超额；无 gate，2025 无 gate 产物）】', flush=True)
    print('| 变体 | n | 均值 | t | 胜率 | H1 | H2 |', flush=True)
    print('|---|---|---|---|---|---|---|', flush=True)
    res, picks = {}, collections.Counter()
    mid = idx[len(idx) // 2]
    for name in ('P0_全主题(对照)', 'S1_量能top3', 'T6_量能top3∩r5>0'):
        vals = []
        for i in range(25, len(idx) - 5):
            r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
            sh = {t: v for t, v in share5.iloc[i].items() if v == v}
            if len(r5d) < 4 or len(sh) < 5:
                continue
            top3 = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:3]}
            pool = set(r5d) if name.startswith('P0') else (top3 if name.startswith('S1') else
                                                           {t for t in top3 if (r5d.get(t) or 0) > 0})
            cand = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in pool]
            if not cand:
                continue
            fv = fwd5.iloc[i].get(cand[0])
            if fv is None or fv != fv:
                continue
            vals.append((idx[i], cand[0], float(fv)))
            if name.startswith('T6'):
                picks[cand[0]] += 1
        if not vals:
            continue
        s = stat([x[2] for x in vals])
        h1 = stat([x[2] for x in vals if x[0] <= mid])
        h2 = stat([x[2] for x in vals if x[0] > mid])
        res[name] = {**s, 'H1': h1, 'H2': h2}
        print('| %s | %s | %s | %s | %s | %s | %s |'
              % (name, s['n'], s['mean'], s['t'], s['pos'], h1.get('mean'), h2.get('mean')), flush=True)
    print('\n【③ T6 选中分布 top8】%s' % '、'.join('%s(%d)' % (t, n) for t, n in picks.most_common(8)), flush=True)
    out = {'period': [a, b], 'variants': res, 't6_picks': dict(picks),
           'monthly_pool': {mo: dict(c) for mo, c in monthly.items()}, 'his_pool': his_pool}
    json.dump(out, open('data/eval_structural_pool_local.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n[local] 结果 → data/eval_structural_pool_local.json', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

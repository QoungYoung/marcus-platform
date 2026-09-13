# -*- coding: utf-8 -*-
"""eval_structural_pool_year.py — 跨期验证：结构池（量能占比 top3 ∩ r5>0）在**其它年份**还成立吗？

**要回答的问题（2026-09-13，A 步）**：2026 样本里"量能占比 top3"几乎恒定 = {半导体、AI、新能源}，
无法区分"这是**结构判据**在起作用"还是"科技常年成交占比高"。
→ 在 **2025** 上跑：他的 2025 池是 **机器人/固态/黄金/AI软件/光伏**（`wolf_period_directions.json` 取证）。
   · 若结构池在 2025 自动选出这些 → **机制证实**（判据随时代改池）；
   · 若仍选出半导体/AI → **证伪**（它只是"科技别名"）。

**口径**：主题 = 13 主题（`MS.load_universe` 概念库，注意成分是**当前**概念表 → 轻度幸存者偏差）；
`share5` = 主题近 5 日成交额 / 全市场近 5 日成交额；`r5` = 近 5 日相对强度（等权复利 − 全市场）；
池 = `share5 top3 ∧ r5>0`；选中 = 池内 r5 top1；度量 = 后 5 日超额（T+1 起算）。
2025 没有 gate 产物（`daily_artifacts` 只有 2026）→ 本页不套 gate。

用法：python jobs/eval_structural_pool_year.py --start 20250101 --end 20251231
"""
import collections
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/backend')
sys.path.insert(0, '/app/jobs')
from app.database import SessionLocal                      # noqa: E402
from sqlalchemy import text                                 # noqa: E402
from app.services import wolf_mainline_select as MS          # noqa: E402
from eval_wolf_pool_prior import DIR2THEME                   # noqa: E402

DATA = os.environ.get('DATA_DIR', '/app/data')
OUT = os.path.join(DATA, 'eval_structural_pool_year.json')


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def main():
    start = sys.argv[sys.argv.index('--start') + 1] if '--start' in sys.argv else '20250101'
    end = sys.argv[sys.argv.index('--end') + 1] if '--end' in sys.argv else '20251231'
    db = SessionLocal()
    uni, _, _ = MS.load_universe()
    db.execute(text("SET LOCAL work_mem = '64MB'"))
    db.execute(text("DROP TABLE IF EXISTS _sy_memb"))
    db.execute(text("CREATE TEMP TABLE _sy_memb (ts_code varchar(16), theme varchar(32))"))
    pairs = [{'ts': c, 'th': th} for th, codes in uni.items() for c in codes]
    for k in range(0, len(pairs), 2000):
        db.execute(text("INSERT INTO _sy_memb (ts_code, theme) VALUES (:ts, :th)"), pairs[k:k + 2000])
    rows = db.execute(text("""
        SELECT b.trade_date AS d, m.theme AS th, avg(b.pct_chg)::float8 AS pc, sum(b.amount)::float8 AS amt
        FROM mkt_bars_daily b JOIN _sy_memb m ON m.ts_code = b.ts_code
        WHERE b.pct_chg IS NOT NULL AND b.trade_date >= :a AND b.trade_date <= :b
        GROUP BY 1, 2
    """), {"a": start, "b": end}).all()
    mrows = db.execute(text("SELECT trade_date d, avg(pct_chg)::float8 pc, sum(amount)::float8 amt "
                            "FROM mkt_bars_daily WHERE pct_chg IS NOT NULL AND trade_date >= :a "
                            "AND trade_date <= :b GROUP BY 1"), {"a": start, "b": end}).all()
    db.close()
    if not rows:
        print('[sy] 该期间没有行情数据（先回填）', flush=True)
        return 2
    g = pd.DataFrame(rows, columns=['d', 'th', 'pc', 'amt'])
    pc = g.pivot_table(index='d', columns='th', values='pc', aggfunc='first').sort_index().astype('float64') / 100.0
    amt = g.pivot_table(index='d', columns='th', values='amt', aggfunc='first').reindex(pc.index).astype('float64')
    mk = pd.Series({r[0]: r[1] for r in mrows}).reindex(pc.index).astype('float64') / 100.0
    mamt = pd.Series({r[0]: r[2] for r in mrows}).reindex(pc.index).astype('float64')
    idx = pc.index.tolist()
    r5 = ((1 + pc).rolling(5).apply(np.prod, raw=True).sub(
        (1 + mk).rolling(5).apply(np.prod, raw=True), axis=0))
    r20 = ((1 + pc).rolling(20).apply(np.prod, raw=True).sub(
        (1 + mk).rolling(20).apply(np.prod, raw=True), axis=0))
    share5 = amt.rolling(5).sum().div(mamt.rolling(5).sum(), axis=0)
    f5 = (1 + pc).rolling(5).apply(np.prod, raw=True).shift(-5)
    f5m = (1 + mk).rolling(5).apply(np.prod, raw=True).shift(-5)
    fwd5 = (f5.sub(f5m, axis=0)) * 100
    print('[sy] %s→%s 共 %d 个交易日 | 主题 %d' % (start, end, len(idx), len(pc.columns)), flush=True)

    # ① 池的月度构成（跨期自动改池？）
    print('\n【① 结构池（量能占比 top3）的月度构成】与**他当月实际方向池**对照', flush=True)
    his_pool = {}
    try:
        pd_ = json.load(open(os.path.join(DATA, 'wolf_period_directions.json'), encoding='utf-8'))
        for per, obj in (pd_.get('periods') or {}).items():
            s = collections.Counter()
            for it in (obj.get('directions') or []):
                th = DIR2THEME.get((it.get('name') or '').strip())
                if th:
                    s[th] += int(it.get('count') or 1)
            his_pool[per] = [t for t, _ in s.most_common(5)]
    except Exception as e:
        print('[sy] 读他的方向池失败 %s' % str(e)[:60], flush=True)
    print('| 月份 | 结构池 top3（按量能占比） | 他该季度的方向池 top5 |', flush=True)
    print('|---|---|---|', flush=True)
    monthly = collections.defaultdict(collections.Counter)
    month_out = {}
    for i in range(20, len(idx)):
        d = idx[i]
        row = {t: v for t, v in share5.iloc[i].items() if v == v}
        if len(row) < 5:
            continue
        for t, _ in sorted(row.items(), key=lambda kv: -kv[1])[:3]:
            monthly[d[:6]][t] += 1
    for mo in sorted(monthly):
        q = '%sQ%d' % (mo[:4], (int(mo[4:]) - 1) // 3 + 1)
        a = '、'.join('%s(%d)' % (t, n) for t, n in monthly[mo].most_common(3))
        b = '、'.join(his_pool.get(q) or []) or '—'
        month_out[mo] = {'pool': a, 'his_pool': b}
        print('| %s | %s | %s |' % (mo, a, b), flush=True)

    # ② 结构池的收益（该年份）
    picks, vals = collections.Counter(), []
    by_pool = collections.defaultdict(list)
    for i in range(25, len(idx) - 5):
        d = idx[i]
        r5d = {t: v for t, v in r5.iloc[i].items() if v == v}
        sh = {t: v for t, v in share5.iloc[i].items() if v == v}
        if len(r5d) < 4 or len(sh) < 5:
            continue
        pool3 = {t for t, _ in sorted(sh.items(), key=lambda kv: -kv[1])[:3]}
        pools = {
            'T6_量能top3∩r5>0': {t for t in pool3 if (r5d.get(t) or 0) > 0},
            'S1_量能top3': pool3,
            'P0_池=全部主题(对照)': set(r5d),
        }
        for name, pool in pools.items():
            cand = [t for t in sorted(r5d, key=lambda t: -r5d[t]) if t in pool]
            if not cand:
                continue
            fv = fwd5.iloc[i].get(cand[0])
            if fv is None or fv != fv:
                continue
            by_pool[name].append((d, cand[0], float(fv)))
            if name == 'T6_量能top3∩r5>0':
                picks[cand[0]] += 1
    print('\n【② 结构池在该期间的表现（后 5 日超额，无 gate）】', flush=True)
    print('| 变体 | n | 均值 | t | 胜率 | H1 | H2 |', flush=True)
    print('|---|---|---|---|---|---|---|', flush=True)
    mid = idx[len(idx) // 2 + 12] if len(idx) > 30 else idx[-1]
    out = {'period': [start, end], 'monthly_pool': month_out, 'variants': {}, 'his_pool': his_pool}
    for name in ('P0_池=全部主题(对照)', 'S1_量能top3', 'T6_量能top3∩r5>0'):
        v = sorted(by_pool.get(name) or [], key=lambda x: x[0])
        if not v:
            continue
        s = stat([x[2] for x in v])
        h1 = stat([x[2] for x in v if x[0] <= mid])
        h2 = stat([x[2] for x in v if x[0] > mid])
        out['variants'][name] = {**s, 'H1': h1, 'H2': h2, 'picks': dict(picks) if name.startswith('T6') else {}}
        print('| %s | %s | %s | %s | %s | %s | %s |'
              % (name, s.get('n'), s.get('mean'), s.get('t'), s.get('pos'), h1.get('mean'), h2.get('mean')),
              flush=True)
    print('\n【③ T6 选中分布 top6】%s' % '、'.join('%s(%d)' % (t, n) for t, n in picks.most_common(6)), flush=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[sy] 结果 → %s' % OUT, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

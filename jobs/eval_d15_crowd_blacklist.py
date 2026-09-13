# -*- coding: utf-8 -*-
"""eval_d15_crowd_blacklist.py — D15 事件研究：生产「拥挤无空间」黑名单到底拦得对不对？

**被检验的机制**（`apps/main_line/rotation_universe.py::build_crowding_blacklist` + `backend/app/api/indicator.py`）：
  1) 子方向层：`crowd_score = crowd_avg / max(crowd_avg) ≥ 0.55` 且 `space_score < 0.55`
     （排除 META_GROUPS=科技/AI(总集)）→ 按 crowd_score 降序取 **top3** = `crowded_top`；
  2) 个股层：`crowded_top` 子方向的成分股中 `n_funds≥4 且 Σfloat≥1%` → 进黑名单；
  3) 个股豁免：命中 `_crowd_space_reason` 的「低位/MID回踩」→ 不硬拦（降级 review/probe）；
     否则 **硬拦**（选股路径直接 blocked）。

**PIT 复现**（全部现成产物，不自造口径）：
  · 子方向 crowd_avg / space / quadrant ← `data/rotation_quadrant_history_pit.json`（13 个真 PIT 时点）
  · 子方向概念列表 ← `data/crowding_pit/rotation_crowd_pit_<date>.json`
  · 个股 n_funds / sum_float ← `data/crowding_pit/stock_crowd_<date>.json`（PIT 快照）
  · 个股空间指标 vh250 / boxpos30 / vm60 / r20 ← `mkt_bars_daily` 逐窗口重算（生产 `_crowd_space_reason` 同规则）
  · 前瞻收益与回撤 ← `mkt_bars_daily`（T+1 起算，市场等权为基准）

输出：被拦 vs 各类对照的后 5/10 日超额、10 日最大回撤、2×2（拥挤 × 空间）交叉、逐次快照明细。
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/backend')
from app.database import SessionLocal                      # noqa: E402
from sqlalchemy import text                                 # noqa: E402

DATA = os.environ.get('DATA_DIR', '/app/data')
OUT = os.path.join(DATA, 'eval_d15_crowd_blacklist.json')
META_GROUPS = {"科技/AI(总集)"}
CROWD_MIN, SPACE_MAX, TOP_N = 0.55, 0.55, 3


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def main():
    quad = json.load(open(os.path.join(DATA, 'rotation_quadrant_history_pit.json'), encoding='utf-8'))
    db = SessionLocal()
    db.execute(text("SET LOCAL work_mem = '64MB'"))
    days = [r[0] for r in db.execute(text("SELECT DISTINCT trade_date FROM mkt_bars_daily ORDER BY 1"))]
    print('[d15] 行情 %d 天 %s…%s' % (len(days), days[0], days[-1]), flush=True)

    db.execute(text("DROP TABLE IF EXISTS _d15_sub"))
    db.execute(text("DROP TABLE IF EXISTS _d15_concept"))
    db.execute(text("DROP TABLE IF EXISTS _d15_stock"))
    db.execute(text("DROP TABLE IF EXISTS _d15_memb"))
    db.execute(text("CREATE TEMP TABLE _d15_sub (snap_date varchar(8), sub varchar(32), crowd_score float8, "
                    "space float8, quadrant varchar(24), is_top int, eff_date varchar(8))"))
    db.execute(text("CREATE TEMP TABLE _d15_concept (snap_date varchar(8), sub varchar(32), concept varchar(64))"))
    db.execute(text("CREATE TEMP TABLE _d15_stock (snap_date varchar(8), ts_code varchar(16), "
                    "n_funds int, sum_float float8)"))

    subs_rows, conc_rows, stock_rows, meta = [], [], [], []
    for d8 in quad['dates']:
        h = (quad['history'] or {}).get(d8) or {}
        if not h:
            continue
        eff = next((x for x in days if x >= d8), None)
        if not eff or days.index(eff) + 11 >= len(days):
            print('[d15] 跳过 %s（无足够前瞻）' % d8, flush=True)
            continue
        mx = max((v.get('crowd_avg') or 0) for v in h.values()) or 1
        scored = []
        for sub, v in h.items():
            if sub in META_GROUPS:
                continue
            cs = (v.get('crowd_avg') or 0) / mx
            sp = v.get('space')
            scored.append((sub, cs, sp, v.get('quadrant')))
        cand = [x for x in scored if x[1] >= CROWD_MIN and (x[2] is not None and x[2] < SPACE_MAX)]
        cand.sort(key=lambda x: -x[1])
        top = {x[0] for x in cand[:TOP_N]}
        for sub, cs, sp, qd in scored:
            subs_rows.append({'snap_date': d8, 'sub': sub, 'crowd_score': cs, 'space': sp,
                              'quadrant': qd, 'is_top': 1 if sub in top else 0, 'eff_date': eff})
        rp = os.path.join(DATA, 'crowding_pit', 'rotation_crowd_pit_%s.json' % d8)
        if os.path.exists(rp):
            ru = (json.load(open(rp, encoding='utf-8')).get('universe') or {})
            # 对照组需要**非拥挤子方向**的成员，所以所有子方向都建（META_GROUPS 除外）
            for sub in ru:
                if sub in META_GROUPS:
                    continue
                for c in ((ru.get(sub) or {}).get('concepts') or []):
                    conc_rows.append({'snap_date': d8, 'sub': sub, 'concept': c})
        sp_ = os.path.join(DATA, 'crowding_pit', 'stock_crowd_%s.json' % d8.replace('-', ''))
        if not os.path.exists(sp_):
            alt = os.path.join(DATA, 'crowding_pit', 'stock_crowd_%s-%s-%s.json' % (d8[:4], d8[4:6], d8[6:]))
            sp_ = alt if os.path.exists(alt) else sp_
        if os.path.exists(sp_):
            for sym, v in ((json.load(open(sp_, encoding='utf-8')).get('stock') or {})).items():
                stock_rows.append({'snap_date': d8, 'ts_code': sym,
                                   'n_funds': int(v.get('n_funds') or 0),
                                   'sum_float': float(v.get('sum_float') or 0)})
        meta.append({'snap_date': d8, 'eff_date': eff, 'crowded_top': sorted(top),
                     'n_cand': len(cand)})
        print('[d15] %s → eff=%s crowded_top=%s' % (d8, eff, sorted(top)), flush=True)

    for name, rows in (('_d15_sub', subs_rows), ('_d15_concept', conc_rows), ('_d15_stock', stock_rows)):
        for k in range(0, len(rows), 1000):
            chunk = rows[k:k + 1000]
            cols = list(chunk[0].keys())
            db.execute(text("INSERT INTO %s (%s) VALUES (%s)" % (
                name, ','.join(cols), ','.join(':' + c for c in cols))), chunk)
    print('[d15] 子方向 %d 行，概念 %d 行，个股快照 %d 行' % (len(subs_rows), len(conc_rows), len(stock_rows)), flush=True)

    # 拥挤子方向成员（概念 → 成分股）
    db.execute(text("""
        CREATE TEMP TABLE _d15_memb AS
        SELECT DISTINCT c.snap_date, c.sub, m.ts_code
        FROM _d15_concept c JOIN stock_concept_map m ON m.concept_name = c.concept
    """))
    n_memb = db.execute(text("SELECT count(*) FROM _d15_memb")).scalar()
    print('[d15] 拥挤子方向成员 %d 行' % n_memb, flush=True)

    # 事件日指标（生产 _crowd_space_reason 同规则）+ 前瞻
    db.execute(text("""
        CREATE TEMP TABLE _d15_ev AS
        SELECT mb.snap_date, mb.sub, s.is_top, s.crowd_score, s.space, s.quadrant, mb.ts_code,
               st.n_funds, st.sum_float,
               b.close AS c0, b.hi250, b.hi30, b.lo30, b.ma60, b.c20b, b.c5f, b.c10f, b.lo10f
        FROM _d15_memb mb
        JOIN _d15_sub s ON s.snap_date = mb.snap_date AND s.sub = mb.sub
        LEFT JOIN _d15_stock st ON st.snap_date = mb.snap_date AND st.ts_code = mb.ts_code
        JOIN (
            SELECT ts_code, trade_date, close,
                   MAX(close) OVER w250 AS hi250,
                   MAX(close) OVER w30   AS hi30,
                   MIN(close) OVER w30   AS lo30,
                   AVG(close) OVER w60   AS ma60,
                   LAG(close, 20) OVER w AS c20b,
                   LEAD(close, 5) OVER w AS c5f,
                   LEAD(close, 10) OVER w AS c10f,
                   MIN(close) OVER (PARTITION BY ts_code ORDER BY trade_date
                                    ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS lo10f
            FROM mkt_bars_daily
            WHERE ts_code IN (SELECT DISTINCT ts_code FROM _d15_memb)
            WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date),
                   w250 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW),
                   w30  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW),
                   w60  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
        ) b ON b.ts_code = mb.ts_code
           AND b.trade_date = (SELECT eff_date FROM _d15_sub s2
                               WHERE s2.snap_date = mb.snap_date AND s2.sub = mb.sub LIMIT 1)
    """))
    # 市场基准（全市场等权前瞻）
    mkt = pd.DataFrame(db.execute(text("""
        WITH b AS (
            SELECT ts_code, trade_date, close,
                   LEAD(close, 5) OVER w AS c5f, LEAD(close, 10) OVER w AS c10f
            FROM mkt_bars_daily
            WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
        )
        SELECT s.snap_date, s.eff_date, avg(b.c5f / b.close - 1) AS m5, avg(b.c10f / b.close - 1) AS m10
        FROM (SELECT DISTINCT snap_date, eff_date FROM _d15_sub) s
        JOIN b ON b.trade_date = s.eff_date
        WHERE b.c5f IS NOT NULL
        GROUP BY 1, 2
    """)).all(), columns=['snap_date', 'eff_date', 'm5', 'm10'])
    df = pd.DataFrame(db.execute(text("SELECT * FROM _d15_ev")).all(),
                      columns=['snap_date', 'sub', 'is_top', 'crowd_score', 'space', 'quadrant', 'ts_code',
                               'n_funds', 'sum_float', 'c0', 'hi250', 'hi30', 'lo30', 'ma60', 'c20b',
                               'c5f', 'c10f', 'lo10f'])
    print('[d15] 事件样本 %d 行 | 市场基准 %d 天' % (len(df), len(mkt)), flush=True)
    db.close()

    for c in ('c0', 'hi250', 'hi30', 'lo30', 'ma60', 'c20b', 'c5f', 'c10f', 'lo10f', 'n_funds', 'sum_float'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df[df['c0'] > 0]
    df['vh'] = (df['c0'] / df['hi250'] - 1) * 100
    df['boxpos'] = (df['c0'] - df['lo30']) / ((df['hi30'] - df['lo30']).replace(0, pd.NA)) * 100
    df['vm60'] = (df['c0'] / df['ma60'] - 1) * 100
    df['r20'] = (df['c0'] / df['c20b'] - 1) * 100
    df['has_space'] = (((df['vh'] <= -30) & (df['boxpos'] <= 35) & (df['vm60'] < 0)) |
                       ((df['vm60'] > 0) & (df['r20'] >= -10) & (df['r20'] < 0)))
    df['heavy'] = (df['n_funds'].fillna(0) >= 4) & (df['sum_float'].fillna(0) >= 1.0)
    df['blocked'] = df['is_top'].astype(bool) & df['heavy'] & (~df['has_space'].astype(bool))
    mk = {r['snap_date']: (r['m5'], r['m10']) for _, r in mkt.iterrows()}
    df['m5'] = df['snap_date'].map(lambda d: mk.get(d, (None, None))[0])
    df['m10'] = df['snap_date'].map(lambda d: mk.get(d, (None, None))[1])
    df['r5'] = (df['c5f'] / df['c0'] - 1 - df['m5']) * 100
    df['r10'] = (df['c10f'] / df['c0'] - 1 - df['m10']) * 100
    df['dd10'] = (df['lo10f'] / df['c0'] - 1) * 100

    groups = {
        '① 被硬拦(拥挤∧重仓∧无空间)': df[df['blocked']],
        '② 同子方向·重仓但有个股空间(豁免)': df[(df['is_top'].astype(bool)) & df['heavy'] & df['has_space'].astype(bool)],
        '③ 同子方向·公募不重仓': df[(df['is_top'].astype(bool)) & ~df['heavy']],
        '④ 非拥挤子方向的同类票': df[~df['is_top'].astype(bool)],
    }
    print('\n【A. 被拦 vs 对照】后 5/10 日超额（%）、后 10 日最大回撤', flush=True)
    print('| 组 | n | r5 | t | r10 | t | 10日回撤 | 胜率(r5) |')
    print('|---|---|---|---|---|---|---|---|')
    out = {'meta': meta, 'groups': {}}
    for name, g in groups.items():
        s5, s10 = stat(g['r5'].dropna().tolist()), stat(g['r10'].dropna().tolist())
        out['groups'][name] = {'r5': s5, 'r10': s10, 'dd10': stat(g['dd10'].dropna().tolist())}
        print('| %s | %s | %s | %s | %s | %s | %s | %s |'
              % (name, s5.get('n'), s5.get('mean'), s5.get('t'), s10.get('mean'), s10.get('t'),
                 out['groups'][name]['dd10'].get('mean'), s5.get('pos')), flush=True)

    print('\n【D. 隔离检验：同子方向内「公募重仓 vs 非重仓」】（控制子方向；判"公募重仓"这一腿是否成立）', flush=True)
    print('| 子方向内 | n | r5 | t | r10 | t |', flush=True)
    print('|---|---|---|---|---|---|', flush=True)
    iso = {}
    for lab, g in (('重仓(n_funds≥4,float≥1%)', df[df['heavy']]),
                   ('非重仓', df[~df['heavy']])):
        s5, s10 = stat(g['r5'].dropna().tolist()), stat(g['r10'].dropna().tolist())
        iso[lab] = {'r5': s5, 'r10': s10}
        print('| %s | %s | %s | %s | %s | %s |' % (lab, s5.get('n'), s5.get('mean'), s5.get('t'),
                                                  s10.get('mean'), s10.get('t')), flush=True)
    # 同空间档内再比 heavy：把"位置"这条腿也控住
    for hs in (False, True):
        g0 = df[df['has_space'].astype(bool) == hs]
        sh = stat(g0[g0['heavy']]['r5'].dropna().tolist())
        sn = stat(g0[~g0['heavy']]['r5'].dropna().tolist())
        iso['space=%s' % ('有' if hs else '无')] = {'heavy': sh, 'non_heavy': sn}
        print('| └ %s空间内：重仓 vs 非重仓 | %s / %s | %s / %s | %s / %s | — | — |'
              % ('有' if hs else '无', sh.get('n'), sn.get('n'), sh.get('mean'), sn.get('mean'),
                 sh.get('t'), sn.get('t')), flush=True)
    out['isolation'] = iso

    print('\n【B. 2×2：子方向是否拥挤 × 个股是否有空间】（r5 超额）', flush=True)
    print('| 拥挤子方向 | 个股空间 | n | r5 | t | r10 | t |', flush=True)
    print('|---|---|---|---|---|---|---|', flush=True)
    for is_top in (1, 0):
        for hs in (False, True):
            g = df[(df['is_top'].astype(int) == is_top) & (df['has_space'].astype(bool) == hs)]
            s5, s10 = stat(g['r5'].dropna().tolist()), stat(g['r10'].dropna().tolist())
            print('| %s | %s | %s | %s | %s | %s | %s |'
                  % ('拥挤' if is_top else '非拥挤', '有空间' if hs else '无空间(高位)',
                     s5.get('n'), s5.get('mean'), s5.get('t'), s10.get('mean'), s10.get('t')), flush=True)

    print('\n【C. 逐次快照：被拦组的 r5（看是否有时段性）】', flush=True)
    print('| 快照 | crowded_top | 被拦 n | 被拦 r5 | 被拦 r10 | 同子方向未拦 r5 |', flush=True)
    print('|---|---|---|---|---|---|', flush=True)
    by_date = {}
    for snp in sorted(df['snap_date'].unique()):
        g = df[df['snap_date'] == snp]
        b = g[g['blocked']]
        o = g[(~g['blocked']) & (g['is_top'].astype(bool))]
        top = next((m['crowded_top'] for m in meta if m['snap_date'] == snp), [])
        s5b, s10b, s5o = stat(b['r5'].dropna().tolist()), stat(b['r10'].dropna().tolist()), stat(o['r5'].dropna().tolist())
        by_date[snp] = {'top': top, 'blocked': s5b, 'blocked_r10': s10b, 'other_in_top': s5o}
        print('| %s | %s | %s | %s | %s | %s |'
              % (snp, '、'.join(top), s5b.get('n'), s5b.get('mean'), s10b.get('mean'), s5o.get('mean')), flush=True)
    out['by_date'] = by_date
    out['cross'] = {}
    for is_top in (1, 0):
        for hs in (False, True):
            g = df[(df['is_top'].astype(int) == is_top) & (df['has_space'].astype(bool) == hs)]
            out['cross']['top%d_space%d' % (is_top, 0 if hs else 1)] = {
                'r5': stat(g['r5'].dropna().tolist()), 'r10': stat(g['r10'].dropna().tolist())}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n[d15] 结果 → %s' % OUT, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

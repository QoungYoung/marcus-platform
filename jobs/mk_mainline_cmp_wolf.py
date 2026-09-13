# -*- coding: utf-8 -*-
"""mk_mainline_cmp_wolf.py — 「他的主线 vs 我们主线」逐月对比清单（2026-09-12）。

四列口径：
  · **他的主线** = `wolf_actual_mainline.payload.doing`（LLM 从他当日语料抽的"在做的方向"）
    → 经 `wolf_dir_theme_map` 归到 13 主题（只统计映射成功的，非主题丢弃）。
  · **我们 gate** = `daily_artifacts.mainline_gate` 中 `gate=true` 的主题（现生产资格闸）。
  · **我们 D1**  = gate 资格 ∩ 近 5 日相对强度 top1（已验收设计，h5 +0.62% t=2.86）。
  · **我们 D1+排除** = 在 D1 的候选池里再去掉"公募持仓最拥挤 1/4 + 成交占比分位最高 1/4"（验收见 eval_ms_exclusions）。

月度各取出现次数 top3；重合度按**当日**算（他的任一方向是否落在我们 top3 内）。
输出 markdown 到 stdout（由主代理写入文档）。
"""
import collections
import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/backend')
from app.database import SessionLocal                      # noqa: E402
from sqlalchemy import text                                 # noqa: E402
from app.services import wolf_mainline_select as MS          # noqa: E402

DATA = os.environ.get('DATA_DIR', '/app/data')


def main():
    db = SessionLocal()
    rows = db.execute(text("SELECT trade_date, ts_code, pct_chg, amount FROM mkt_bars_daily")).all()
    df = pd.DataFrame(rows, columns=['d', 'ts', 'pc', 'amt'])
    df['pc'] = df['pc'].astype(float) / 100.0
    df['amt'] = df['amt'].astype(float)
    wide = df.pivot_table(index='d', columns='ts', values='pc', aggfunc='first').sort_index()
    amt = df.pivot_table(index='d', columns='ts', values='amt', aggfunc='sum').reindex(wide.index).fillna(0.0)
    idx = wide.index.tolist()
    px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in idx}
    uni, lead, allc = MS.load_universe()
    if not uni:
        print('主题成分为空', flush=True)
        return 2

    snaps = {}
    for p in glob.glob(os.path.join(DATA, 'crowding_pit', 'stock_crowd_*.json')):
        try:
            dd = json.load(open(p, encoding='utf-8'))
            day = (dd.get('date') or '').replace('-', '')
            if len(day) == 8 and dd.get('stock'):
                snaps[day] = dd['stock']
        except Exception:
            pass
    sdays = sorted(snaps)
    try:
        rule = (json.load(open(os.path.join(DATA, 'crowding_blacklist.json'), encoding='utf-8')).get('rule') or {})
    except Exception:
        rule = {}
    nf_min, fl_min = float(rule.get('n_funds_min', 4)), float(rule.get('float_pct_min', 1.0))

    mk_amt5 = amt.sum(axis=1).rolling(5).sum()
    volpct = {}
    for th, codes in uni.items():
        cols = [c for c in codes if c in amt.columns]
        if cols:
            s = amt[cols].sum(axis=1).rolling(5).sum() / mk_amt5.replace(0, pd.NA)
            volpct[th] = s.rolling(120, min_periods=40).rank(pct=True).to_dict()

    def snap_for(d8):
        av = [s for s in sdays if s <= d8]
        return snaps[av[-1]] if av else None

    def hold_float_avg(d8, th):
        sn = snap_for(d8)
        if not sn:
            return None
        pres = [c for c in uni[th] if c in sn]
        if not pres:
            return None
        return sum(float(sn[c].get('sum_float') or 0) for c in pres) / len(pres)

    def win(th, i, lo, hi):
        ser = []
        for k in range(lo, hi + 1):
            if 0 <= k < len(idx):
                b = MS.basket_return(px.get(idx[k], {}), uni.get(th) or [])
                mm = MS.basket_return(px.get(idx[k], {}), allc)
                ser.append(None if (b is None or mm is None) else (b - mm))
        return MS.compound(ser)

    dmap = {r['dir_text']: r['theme'] for r in db.execute(
        text("SELECT dir_text, theme FROM wolf_dir_theme_map")).mappings().all()}
    his = {}
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline "
                             "WHERE status='ok'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        s = sorted({dmap.get((x.get('dir') or '').strip()) for x in (p.get('doing') or [])} - {None, '非主题'})
        if s:
            his[r['trade_date']] = s
    gates = {}
    for r in db.execute(text("SELECT trade_date, payload FROM daily_artifacts "
                             "WHERE artifact_key='mainline_gate'")).mappings().all():
        p = r['payload'] if isinstance(r['payload'], dict) else json.loads(r['payload'] or '{}')
        gates[r['trade_date']] = [x['theme'] for x in (p.get('rows') or []) if x.get('gate')]

    stats = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    hit = collections.Counter()
    by_theme = collections.defaultdict(collections.Counter)
    for i in range(12, len(idx)):
        d = idx[i]
        r5 = {th: win(th, i, i - 4, i) for th in uni}
        r5 = {t: v for t, v in r5.items() if v is not None}
        if len(r5) < 4:
            continue
        mon = d[:6]
        gs = [t for t in (gates.get(d) or []) if t in r5] or list(r5)
        pool = sorted(gs, key=lambda t: -r5[t])
        d1 = pool[:1]
        for t in gs:          # gate 全量（不限前3）
            stats[mon]['gate'][t] += 1
            by_theme['gate'][t] += 1
        for t in d1:
            by_theme['d1'][t] += 1
        # D1+排除
        cand = list(pool)
        vals = sorted([(t, hold_float_avg(d, t)) for t in cand if hold_float_avg(d, t) is not None],
                      key=lambda kv: -kv[1])
        if len(vals) >= 3:
            bad = {t for t, _ in vals[:max(1, len(vals) // 4)]}
            cand = [t for t in cand if t not in bad] or cand
        vals = sorted([(t, float(volpct[t][d])) for t in cand
                       if (volpct.get(t) or {}).get(d) is not None and volpct[t][d] == volpct[t][d]],
                      key=lambda kv: -kv[1])
        if len(vals) >= 3:
            bad = {t for t, _ in vals[:max(1, len(vals) // 4)]}
            cand = [t for t in cand if t not in bad] or cand
        d1x = cand[:1]
        for t in d1x:
            by_theme['d1x'][t] += 1
            stats[mon]['d1x'][t] += 1
        for t in pool[:3]:
            stats[mon]['pool3'][t] += 1
        for t in d1:
            stats[mon]['d1'][t] += 1
        if his.get(d):
            hit['days'] += 1
            for t in his[d]:
                stats[mon]['his'][t] += 1
                by_theme['his'][t] += 1
            if set(his[d]) & set(pool[:3]):
                hit['pool3'] += 1
            if set(his[d]) & set(d1):
                hit['d1'] += 1
            if set(his[d]) & set(d1x):
                hit['d1x'] += 1
            if set(his[d]) & set(gs):
                hit['gate'] += 1

    def top3(c):
        return '、'.join('%s(%d)' % (t, n) for t, n in c.most_common(3)) or '—'

    print('### 表1 逐月主线对照（各列当月出现次数 top3）')
    print('| 月份 | 他的主线 | 我们 gate 高频 | 我们 D1(gate∩r5) | D1+排除型 |')
    print('|---|---|---|---|---|')
    for mon in sorted(stats):
        s = stats[mon]
        print('| %s | %s | %s | %s | %s |'
              % (mon, top3(s['his']), top3(s['gate']), top3(s['pool3']), top3(s['d1x'])))
    print()
    print('### 表2 全期 13 主题分布（出现天数）')
    print('| 主题 | 他 | 我们 gate | 我们 D1 选中 | D1+排除 选中 |')
    print('|---|---|---|---|---|')
    allth = sorted(uni, key=lambda t: -by_theme['his'][t])
    for t in allth:
        print('| %s | %d | %d | %d | %d |' % (t, by_theme['his'][t], by_theme['gate'][t],
                                               by_theme['d1'][t], by_theme['d1x'][t]))
    print()
    nd = max(1, hit['days'])
    print('### 表3 当日重合度（分母=有他方向的 %d 天）' % hit['days'])
    print('| 口径 | 他的方向落在其中 | 命中率 | 随机基线 |')
    print('|---|---|---|---|')
    print('| 我们 gate（全部确认主题） | %d | %.0f%% | — |' % (hit['gate'], 100.0 * hit['gate'] / nd))
    print('| 我们 D1 的 top1 | %d | %.0f%% | 7.7%% |' % (hit['d1'], 100.0 * hit['d1'] / nd))
    print('| 我们 D1 的 top3 | %d | %.0f%% | 23%% |' % (hit['pool3'], 100.0 * hit['pool3'] / nd))
    print('| D1+排除 的 top1 | %d | %.0f%% | 7.7%% |' % (hit['d1x'], 100.0 * hit['d1x'] / nd))
    db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

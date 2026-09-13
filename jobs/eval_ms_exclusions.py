# -*- coding: utf-8 -*-
"""eval_ms_exclusions.py — 给方向层加「排除型」数据的验收（2026-09-12）。

**他的原话依据**
  · 避开公募重仓：「避开海外链，特别是那几个大公募持仓个股」（2026-08-26）；
    「他**基金持仓多的就上不去**，这是大概率事件」（2026-08-27）
  · 拥挤度：「光+半导体加起来是市场 50% 成交量，现在降到 40%…要降到 25%-30% 才可能重新走起来」（2026-08-24）

**数据一律复用现成模块产物，不自己造口径**：
  · 公募持仓拥挤 = `data/crowding_pit/stock_crowd_<YYYY-MM-DD>.json`（19 期，2025-12-01→2026-08-11），
    由 `apps/main_line/build_crowding.py` 产出，口径 "fund_share T-1 top60 + ann_date<=date + 每基金 top10"。
    PIT 取法：日 D 用 **date ≤ D 的最近一期**（快照本身即 T-1 份额 + 已披露公告）。
  · 「基金持仓多」的判定阈值 = **直接读 `data/crowding_blacklist.json` 的 rule**
    （`n_funds_min=4, float_pct_min=1.0`），逐日在 PIT 快照上重算 —— 不用"当前黑名单"回看历史。
  · 交易拥挤 = `mkt_bars_daily.amount` 的主题 5 日成交额占全市场比，取其在**自身近 120 日**中的分位。

先回答"排除型数据本身有没有预测力"（IC），再做变体（资格闸 ∩ 近5日相对强度 top1，逐步加排除）：
  V0 基线｜V1 +公募持仓排除(去最拥挤 1/4)｜V2 +成交拥挤排除(去最拥挤 1/4)｜V3 两者都加
度量：所选方向后 5 日超额（D+1 起）、t、胜率，以及"他的方向落在我们 top1/top3"（随机 7.7%/23%）。

用法：python jobs/eval_ms_exclusions.py [--end YYYYMMDD]
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
OUT = os.path.join(DATA, 'eval_ms_exclusions.json')


def stat(v):
    n = len(v)
    if not n:
        return {'n': 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {'n': n, 'mean': round(m, 3), 't': round(m / (sd / n ** 0.5), 2) if sd else None,
            'pos': round(sum(1 for x in v if x > 0) / n, 3)}


def rank_corr(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    def rk(v):
        o = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for pos, i in enumerate(o):
            r[i] = pos + 1.0
        return r
    a, b = rk(xs), rk(ys)
    ma, mb = sum(a) / n, sum(b) / n
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db_ = sum((y - mb) ** 2 for y in b) ** 0.5
    if not da or not db_:
        return None
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (da * db_)


def load_crowd_snapshots():
    """读现成的 PIT 公募持仓快照（build_crowding 产物）。"""
    snaps = {}
    for p in glob.glob(os.path.join(DATA, 'crowding_pit', 'stock_crowd_*.json')):
        try:
            d = json.load(open(p, encoding='utf-8'))
            day = (d.get('date') or '').replace('-', '')
            if len(day) == 8 and d.get('stock'):
                snaps[day] = d['stock']
        except Exception as e:
            print('[ex] 快照读取失败 %s: %s' % (os.path.basename(p), str(e)[:60]), flush=True)
    return snaps


def load_blacklist_rule():
    """复用黑名单自己的规则参数（不自己另定阈值）。"""
    try:
        b = json.load(open(os.path.join(DATA, 'crowding_blacklist.json'), encoding='utf-8'))
        r = b.get('rule') or {}
        return {'n_funds_min': float(r.get('n_funds_min', 4)),
                'float_pct_min': float(r.get('float_pct_min', 1.0)),
                'concepts': b.get('concepts') or [], 'subs': b.get('subs') or [],
                'ts': b.get('ts')}
    except Exception as e:
        print('[ex] 黑名单读取失败 %s' % str(e)[:60], flush=True)
        return {'n_funds_min': 4.0, 'float_pct_min': 1.0, 'concepts': [], 'subs': [], 'ts': None}


def main(end=None):
    db = SessionLocal()
    rows = db.execute(text("SELECT trade_date, ts_code, pct_chg, amount FROM mkt_bars_daily")).all()
    df = pd.DataFrame(rows, columns=['d', 'ts', 'pc', 'amt'])
    df['pc'] = df['pc'].astype(float) / 100.0
    df['amt'] = df['amt'].astype(float)
    wide = df.pivot_table(index='d', columns='ts', values='pc', aggfunc='first').sort_index()
    amt = df.pivot_table(index='d', columns='ts', values='amt', aggfunc='sum').reindex(wide.index).fillna(0.0)
    idx = wide.index.tolist()
    if end:
        idx = [d for d in idx if d <= str(end)]
        wide, amt = wide.loc[idx], amt.loc[idx]
    px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in idx}
    mkt = {d: (sum(px[d].values()) / len(px[d])) for d in idx if px.get(d)}

    uni, lead, allc = MS.load_universe()
    if not uni:
        print('[ex] 主题成分为空（stock_pool.db 不可用？），退出', flush=True)
        return 2
    snaps = load_crowd_snapshots()
    rule = load_blacklist_rule()
    sdays = sorted(snaps.keys())
    print('[ex] 行情 %d 天(%s…%s) | 主题 %d | 公募快照 %d 期(%s…%s) | 黑名单规则 n_funds>=%g float>=%g%%'
          % (len(idx), idx[0], idx[-1], len(uni), len(sdays),
             sdays[0] if sdays else '-', sdays[-1] if sdays else '-',
             rule['n_funds_min'], rule['float_pct_min']), flush=True)

    mk_amt5 = amt.sum(axis=1).rolling(5).sum()
    volpct = {}
    for th, codes in uni.items():
        cols = [c for c in codes if c in amt.columns]
        if not cols:
            continue
        s = amt[cols].sum(axis=1).rolling(5).sum() / mk_amt5.replace(0, pd.NA)
        volpct[th] = s.rolling(120, min_periods=40).rank(pct=True).to_dict()

    def snap_for(d8):
        av = [s for s in sdays if s <= d8]
        return snaps[av[-1]] if av else None

    def hold_metrics(d8, th):
        sn = snap_for(d8)
        if not sn:
            return None
        pres = [c for c in uni[th] if c in sn]
        if not pres:
            return None
        n_crowd = sum(1 for c in pres
                      if float(sn[c].get('n_funds') or 0) >= rule['n_funds_min']
                      and float(sn[c].get('sum_float') or 0) >= rule['float_pct_min'])
        return {'n_present': len(pres),
                'float_avg': sum(float(sn[c].get('sum_float') or 0) for c in pres) / len(pres),
                'mkv_avg': sum(float(sn[c].get('sum_mkv_yi') or 0) for c in pres) / len(pres),
                'crowd_share': n_crowd / len(pres)}

    def fwd_excess(d, th, h=5):
        if th not in uni or d not in idx:
            return None
        i = idx.index(d)
        seg = idx[i + 1:i + 1 + h]
        if len(seg) < h:
            return None
        c = 1.0
        for dd in seg:
            v = [px[dd][x] for x in uni[th] if x in px.get(dd, {})]
            if v:
                c *= (1 + sum(v) / len(v))
        m = 1.0
        for dd in seg:
            m *= (1 + mkt.get(dd, 0.0))
        return (c - m) * 100

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

    ics = collections.defaultdict(list)
    var_pick = collections.defaultdict(list)
    pool_top3 = {}          # 当日 pool 前三（用于统计"他的方向落在我们 top3"）
    bucket = collections.defaultdict(list)
    for i in range(12, len(idx)):
        d = idx[i]
        if i + 5 >= len(idx):
            break
        r5 = {th: win(th, i, i - 4, i) for th in uni}
        r5 = {t: v for t, v in r5.items() if v is not None}
        if len(r5) < 4:
            continue
        hm = {th: hold_metrics(d, th) for th in r5}
        fwd = {th: fwd_excess(d, th) for th in r5}
        for key in ('float_avg', 'crowd_share', 'mkv_avg'):
            xs, ys = [], []
            for th in r5:
                if hm.get(th) and fwd.get(th) is not None:
                    xs.append(hm[th][key]); ys.append(fwd[th])
            c = rank_corr(xs, ys)
            if c is not None:
                ics[key].append(c)
        xs, ys = [], []
        for th in r5:
            vp = (volpct.get(th) or {}).get(d)
            if vp is not None and vp == vp and fwd.get(th) is not None:
                xs.append(float(vp)); ys.append(fwd[th])
        c = rank_corr(xs, ys)
        if c is not None:
            ics['volpct'].append(c)
        # 分档（他两句话的直接检验）
        vals = sorted([(hm[t]['float_avg'], fwd[t]) for t in r5
                       if hm.get(t) and fwd.get(t) is not None], key=lambda kv: kv[0])
        if len(vals) >= 3:
            k = max(1, len(vals) // 3)
            bucket['低拥挤1/3'] += [f for _, f in vals[:k]]
            bucket['中1/3'] += [f for _, f in vals[k:-k]]
            bucket['高拥挤1/3'] += [f for _, f in vals[-k:]]

        gs = [t for t in (gates.get(d) or []) if t in r5] or list(r5)
        pool = sorted(gs, key=lambda t: -r5[t])
        pool_top3[d] = pool[:3]

        def pick(exc_hold, exc_vol):
            cand = list(pool)
            if exc_hold and len(cand) > 2:
                vals = sorted([(t, hm[t]['float_avg']) for t in cand if hm.get(t)], key=lambda kv: -kv[1])
                if len(vals) >= 3:
                    bad = {t for t, _ in vals[:max(1, len(vals) // 4)]}
                    cand = [t for t in cand if t not in bad] or cand
            if exc_vol and len(cand) > 2:
                vals = sorted([(t, float(volpct[t][d])) for t in cand
                               if (volpct.get(t) or {}).get(d) is not None
                               and volpct[t][d] == volpct[t][d]], key=lambda kv: -kv[1])
                if len(vals) >= 3:
                    bad = {t for t, _ in vals[:max(1, len(vals) // 4)]}
                    cand = [t for t in cand if t not in bad] or cand
            return cand[0] if cand else None

        for vname, eh, ev in (('V0_baseline', False, False), ('V1_hold', True, False),
                              ('V2_vol', False, True), ('V3_both', True, True)):
            t = pick(eh, ev)
            if t and fwd_excess(d, t) is not None:
                var_pick[vname].append((d, t, fwd_excess(d, t)))


    # ④ 个股层面（他原话的原始层面：「那几个大公募持仓个股」「基金持仓多的就上不去」）
    st_ic = collections.defaultdict(list)
    cond = collections.defaultdict(lambda: collections.defaultdict(list))
    for i in range(12, len(idx)):
        d = idx[i]
        if i + 5 >= len(idx):
            break
        sn = snap_for(d)
        if not sn:
            continue
        seg = idx[i + 1:i + 6]
        m5 = 1.0
        for dd in seg:
            m5 *= (1 + mkt.get(dd, 0.0))
        m5 = (m5 - 1) * 100
        for key in ('n_funds', 'sum_float', 'sum_mkv_yi'):
            xs, ys = [], []
            for code, info in sn.items():
                if code not in px.get(d, {}):
                    continue
                c, ok = 1.0, True
                for dd in seg:
                    v = px.get(dd, {}).get(code)
                    if v is None:
                        ok = False
                        break
                    c *= (1 + v)
                if not ok:
                    continue
                xs.append(float(info.get(key) or 0))
                ys.append((c - 1) * 100 - m5)
            cc = rank_corr(xs, ys) if len(xs) >= 30 else None
            if cc is not None:
                st_ic[key].append(cc)
        # 条件式：主题位置（近20日超额）高/低 + 拥挤度
        r20 = {th: win(th, i, i - 19, i) for th in uni}
        valid = [(th, r20[th]) for th in uni if r20.get(th) is not None]
        if len(valid) >= 6:
            med = sorted(v for _, v in valid)[len(valid) // 2]
            for half, sel in (('高位', [th for th, v in valid if v >= med]),
                              ('低位', [th for th, v in valid if v < med])):
                xs, ys = [], []
                for th in sel:
                    hm = hold_metrics(d, th)
                    fx = fwd_excess(d, th)
                    if hm and fx is not None:
                        xs.append(hm['float_avg']); ys.append(fx)
                cc = rank_corr(xs, ys) if len(xs) >= 4 else None
                if cc is not None:
                    cond[half]['float_avg_IC'].append(cc)
                xs, ys = [], []
                for th in sel:
                    vp = (volpct.get(th) or {}).get(d)
                    fx = fwd_excess(d, th)
                    if vp is not None and vp == vp and fx is not None:
                        xs.append(float(vp)); ys.append(fx)
                cc = rank_corr(xs, ys) if len(xs) >= 4 else None
                if cc is not None:
                    cond[half]['volpct_IC'].append(cc)

    out = {'data': {'bars_days': len(idx), 'snapshots': sdays, 'themes': len(uni),
                    'blacklist_rule': {k: rule[k] for k in ('n_funds_min', 'float_pct_min')},
                    'blacklist_ts': rule.get('ts')},
           'ic': {k: stat(v) for k, v in ics.items()},
           'by_crowd_bucket': {k: stat(v) for k, v in bucket.items()},
           'stock_level_ic': {k: stat(v) for k, v in st_ic.items()},
           'conditional_ic': {h: {k: stat(v) for k, v in dd.items()} for h, dd in cond.items()},
           'variants': {}}
    print('\n【① 排除型数据本身有没有预测力】横截面秩相关 IC（拥挤指标 vs 主题后5日超额）', flush=True)
    for k, v in out['ic'].items():
        print('   %-12s n=%-4s meanIC=%-8s t=%-6s' % (k, v.get('n'), v.get('mean'), v.get('t')), flush=True)
    print('\n【② 他的两句话的直接检验】按公募持仓拥挤分档看后 5 日超额', flush=True)
    for k, v in out['by_crowd_bucket'].items():
        print('   %-10s n=%-4s mean=%-8s t=%-6s 胜率=%-6s'
              % (k, v.get('n'), v.get('mean'), v.get('t'), v.get('pos')), flush=True)

    print('\n【④ 个股层面】公募持仓指标 vs 个股后5日超额（样本=快照内公募重仓股，剔市场）', flush=True)
    for k, v in out['stock_level_ic'].items():
        print('   %-12s n=%-4s meanIC=%-8s t=%-6s' % (k, v.get('n'), v.get('mean'), v.get('t')), flush=True)
    print('\n【⑤ 条件式】主题位置（近20日超额）高/低位的拥挤度 IC', flush=True)
    for h, dd in out['conditional_ic'].items():
        for k, v in dd.items():
            print('   %s %-14s n=%-4s meanIC=%-8s t=%-6s' % (h, k, v.get('n'), v.get('mean'), v.get('t')), flush=True)

    print('\n【③ 变体验收】所选方向后 5 日超额（%）；他方向落 top1 / top3 一栏分母=有他方向的交易日', flush=True)
    print('| 变体 | n | 均值 | t | 胜率 | 他落top1 | 他落top3 |')
    print('|---|---|---|---|---|---|---|')
    for vname in ('V0_baseline', 'V1_hold', 'V2_vol', 'V3_both'):
        picks = var_pick[vname]
        s = stat([f for _, _, f in picks])
        hd = [d for d, _, _ in picks if his.get(d)]
        n1 = sum(1 for d, t, _ in picks if t in (his.get(d) or []))
        n3 = sum(1 for d, t, _ in picks if set(his.get(d) or []) & set((pool_top3.get(d) or [t])[:3]))
        v = {'metric': 'h5_excess_pct', **s, 'n_days': len(picks), 'n_his_days': len(hd),
             'his_top1_hit': round(n1 / max(1, len(hd)), 3),
             'his_top3_hit': round(n3 / max(1, len(hd)), 3)}
        out['variants'][vname] = v
        print('| %s | %s | %s | %s | %s | %d/%d=%.0f%% | %d/%d=%.0f%% |'
              % (vname, s.get('n'), s.get('mean'), s.get('t'), s.get('pos'),
                 n1, len(hd), 100.0 * n1 / max(1, len(hd)),
                 n3, len(hd), 100.0 * n3 / max(1, len(hd))), flush=True)
    try:
        json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n[ex] 结果 → %s' % OUT, flush=True)
    except Exception as e:
        print('[ex] 写结果失败 %s' % str(e)[:60], flush=True)
    db.close()
    return 0


if __name__ == '__main__':
    e = None
    if '--end' in sys.argv:
        e = sys.argv[sys.argv.index('--end') + 1]
    sys.exit(main(e))

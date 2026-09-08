# -*- coding: utf-8 -*-
"""replay_theme_gate.py — 主题主线门 PIT 回放(农业案例 2026-07-24~09-08)
对每个关键日 d(截至当日数据): 结构 GATE(B_only ratio>=0.35) + heat_v2 热度(mf5+mf_accel+rel,
默认权重) rank -> 判断系统何时能识别该主题为主线候选(TOP2∩PASS=confirmed_candidate)。
用法: python3 replay_theme_gate.py [--theme 农业] [--dates ...]
"""
import sys, os, json, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    theme = '农业'
    dates8 = ['20260724','20260731','20260803','20260807','20260812','20260818','20260821','20260824','20260827','20260901','20260904','20260908']
    args = sys.argv[1:]
    if '--theme' in args: theme = args[args.index('--theme') + 1]
    if '--dates' in args: dates8 = args[args.index('--dates') + 1].split(',')
    from trend_confirm import load_by_name, judge_series, _load_params, TREND_CFG
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEMES
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    cfg = _load_params(dict(TREND_CFG), os.path.join(DATA, 'trend_confirm_params.json'))
    by = load_by_name(os.path.join(DATA, 'concept_long.json'))
    all_days = sorted({d for v in by.values() for d in v.get('dates', [])})
    # 成分
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    th_members = {}
    for th, cons in THEME_CONCEPTS.items():
        ts = []
        for c in cons:
            for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,)):
                ts.append(r[0])
        th_members[th] = list(dict.fromkeys(ts))
    # 拉 moneyflow: 覆盖窗口(all_days 中 <= max date 且 >= min-20)
    d_min = all_days[max(0, all_days.index(dates8[0]) - 25)]
    d_max = dates8[-1]
    mf_days = [d for d in all_days if d_min <= d <= d_max]
    mf = {}
    for d in mf_days:
        try:
            df = pro.moneyflow_dc(trade_date=d, fields='ts_code,net_amount')
            if df is not None and not df.empty:
                mf[d] = {str(r['ts_code']): float(r['net_amount']) for _, r in df.iterrows()}
        except Exception as e:
            print('mf err', d, str(e)[:60], flush=True)
        time.sleep(0.12)
    print('moneyflow', len(mf), 'days', mf_days[0], '-', mf_days[-1], flush=True)
    themes = [th for th in MAIN_THEMES if th in THEME_CONCEPTS]
    def pct_rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        rk = [0.0] * len(vals)
        for pos, i in enumerate(order): rk[i] = (pos + 1) / len(vals)
        return dict(zip(vals, rk))
    def theme_idx_close(th, upto):
        import numpy as np
        pcts = []
        for c in THEME_CONCEPTS[th]:
            v = by.get(c)
            if not v: continue
            cl = v['close']; ds = v['dates']
            n = sum(1 for x in ds if x <= upto)
            if n < 25: continue
            cc = cl[:n]; base = cc[0]
            if base <= 0: continue
            pcts.append([(x / base - 1.0) * 100.0 for x in cc])
        if not pcts: return None
        m = len(pcts[0])
        return [sum(p[i] for p in pcts) / len(pcts) for i in range(m)]
    def gate_ratio(th, upto):
        cn = cj = 0
        for c in THEME_CONCEPTS[th]:
            v = by.get(c)
            if not v: continue
            ds = v['dates']; n = sum(1 for x in ds if x <= upto)
            if n < 25: continue
            st = judge_series(v['close'][:n], cfg).get('stage')
            if st in ('confirmed', 'suspect', 'not_confirmed'):
                cj += 1
                if st == 'confirmed': cn += 1
        return (cn / cj if cj else 0.0), cj
    rows = []
    for d in dates8:
        w = [x for x in mf_days if x <= d][-10:]
        if len(w) < 6:
            print(d, '窗口不足'); continue
        w5 = w[-5:]
        raw = {}
        for th in themes:
            mem = th_members.get(th) or []
            s5 = sum(mf[x].get(ts, 0.0) for x in w5 for ts in mem)
            s10 = sum(mf[x].get(ts, 0.0) for x in w for ts in mem)
            idx = theme_idx_close(th, d)
            r20 = (idx[-1] / idx[-21] - 1) if idx and len(idx) > 21 else 0.0
            raw[th] = (s5, s10, r20)
        # percentile
        def pr(k, f):
            vals = [f(raw[th][k], th) for th in themes]
            return {th: (i + 1) / len(vals) for i, th in enumerate(sorted(themes, key=lambda t: f(raw[t][k], t)))}
        rank_map = {}
        for th in themes:
            s5, s10, r20 = raw[th]
            mf5p = sum(1 for t in themes if raw[t][0] <= s5) / len(themes)
            accp = sum(1 for t in themes if (raw[t][0]/5 - (raw[t][1]-raw[t][0])/5) <= (s5/5 - (s10-s5)/5)) / len(themes)
            relp = sum(1 for t in themes if raw[t][2] <= r20) / len(themes)
            sc = 0.4 * mf5p + 0.3 * relp + 0.3 * accp
            rank_map[th] = (sc, mf5p, relp, accp)
        ranked = sorted(themes, key=lambda t: -rank_map[t][0])
        g, cj = gate_ratio(theme, d)
        tgt = rank_map[theme]
        rows.append({'date': d, 'agri_gate_pass': g >= 0.35, 'agri_gate_ratio': round(g, 2), 'agri_judgeable': cj,
                     'agri_heat_score': round(tgt[0], 3),
                     'agri_rank': ranked.index(theme) + 1,
                     'top2': [ranked[0], ranked[1]],
                     'agri_mf5p': round(tgt[1], 2), 'agri_relp': round(tgt[2], 2), 'agri_accp': round(tgt[3], 2)})
        verdict = 'confirmed_candidate' if (ranked.index(theme) + 1) <= 2 and g >= 0.35 else (
            'watch' if (ranked.index(theme) + 1) <= 2 else ('reserve' if g >= 0.35 else 'none'))
        print('%s 农业 GATE=%s(%.2f/%d) heat=%.3f rank=%d/%d top2=%s -> %s' % (
            d, 'PASS' if g >= 0.35 else 'FAIL', g, cj, tgt[0], ranked.index(theme) + 1, len(themes),
            ranked[0] + ',' + ranked[1], verdict), flush=True)
    json.dump({'theme': theme, 'rows': rows}, open(os.path.join(DATA, 'replay_agri_gate_20260908.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE /app/data/replay_agri_gate_20260908.json')

if __name__ == '__main__':
    main()

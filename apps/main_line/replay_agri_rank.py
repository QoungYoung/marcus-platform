# -*- coding: utf-8 -*-
"""replay_agri_rank.py — 农业 heat_v2 v3(含ETF份额) 每日 PIT 回放(2026-06-20~09-08)
找: 农业 heat rank1 首日 / confirmed_candidate(rank<=2 且结构GATE PASS) 首日 -> 能否抓住(对照 8-24低吸/8-27突破)
产物: /app/data/replay_agri_rank.json"""
import sys, os, json, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')
W = {'mf5': 0.35, 'rel': 0.25, 'mf_accel': 0.2, 'etf': 0.2}

def main():
    from trend_confirm import load_by_name, judge_series, _load_params, TREND_CFG
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEMES
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    cfg = _load_params(dict(TREND_CFG), os.path.join(DATA, 'trend_confirm_params.json'))
    by = load_by_name(os.path.join(DATA, 'concept_long.json'))
    days = sorted({d for v in by.values() for d in v.get('dates', []) if '20260601' <= d <= '20260908'})
    # ETF 映射(Pi 审核版) + 份额全区间
    efmap = json.load(open(os.path.join(DATA, 'etf_theme_map_pi.json'), encoding='utf-8'))['themes']
    theme_etfs = {a['theme']: [e['ts_code'] for e in a.get('primary', [])][:2] for a in efmap}
    etf_series = {}
    for th, tss in theme_etfs.items():
        for ts in tss:
            if ts in etf_series: continue
            try:
                df = pro.fund_share(ts_code=ts, start_date='20260601', end_date='20260908')
                ser = {}
                if df is not None and not df.empty:
                    for _, r in df.iterrows(): ser[str(r['trade_date']).replace('-', '')] = float(r['fd_share'])
                etf_series[ts] = ser
            except Exception:
                etf_series[ts] = {}
            time.sleep(0.1)
    print('etf series', len(etf_series), flush=True)
    # moneyflow 全区间逐日
    mf = {}
    for d in days:
        try:
            df = pro.moneyflow_dc(trade_date=d, fields='ts_code,net_amount')
            if df is not None and not df.empty: mf[d] = {str(r['ts_code']): float(r['net_amount']) for _, r in df.iterrows()}
        except Exception as e:
            print('mf err', d, str(e)[:50], flush=True)
        time.sleep(0.1)
    print('moneyflow days', len(mf), days[0], '-', days[-1], flush=True)
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    th_members = {}
    for th, cons in THEME_CONCEPTS.items():
        ts = []
        for c in cons:
            for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,)): ts.append(r[0])
        th_members[th] = list(dict.fromkeys(ts))
    themes = [th for th in MAIN_THEMES if th in THEME_CONCEPTS]
    # 逐日计算
    def pct(vals):
        s = sorted(range(len(vals)), key=lambda i: vals[i])
        rk = [0.0] * len(vals)
        for pos, i in enumerate(s): rk[i] = (pos + 1) / len(vals)
        return rk
    def theme_idx_r20(th, upto):
        pcts = []
        for c in THEME_CONCEPTS[th]:
            v = by.get(c)
            if not v: continue
            ds = v['dates']; cl = v['close']
            n = sum(1 for x in ds if x <= upto)
            if n < 25: continue
            cc = cl[:n]; base = cc[0]
            if base <= 0: continue
            pcts.append([(x / base - 1.0) * 100.0 for x in cc])
        if not pcts: return None
        m = len(pcts[0])
        idx = [sum(p[i] for p in pcts) / len(pcts) for i in range(m)]
        return (idx[-1] / idx[-21] - 1.0) if len(idx) > 21 else 0.0
    def etf_d20(th, upto):
        ta = tb = 0.0; ok = False
        for ts in theme_etfs.get(th, []):
            ser = etf_series.get(ts) or {}
            dsl = sorted(x for x in ser if x <= upto)
            if len(dsl) <= 20: continue
            ta += ser[dsl[-1 - 20]]; tb += ser[dsl[-1]]; ok = True
        return ((tb / ta - 1.0) if (ok and ta) else None)
    def gate_pass(th, upto):
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
    firsts = {'rank1': None, 'confirmed': None}
    for d in days[10:]:   # 留 10 日窗口
        w = [x for x in days if x <= d][-10:]
        raw = {}
        for th in themes:
            mem = th_members.get(th) or []
            s5 = sum(mf[x].get(t, 0.0) for x in w[-5:] for t in mem)
            s10 = sum(mf[x].get(t, 0.0) for x in w for t in mem)
            raw[th] = {'s5': s5, 's10': s10, 'r20': theme_idx_r20(th, d), 'etf': etf_d20(th, d)}
        sc = {}
        for th in themes:
            r = raw[th]
            mf5p = sum(1 for t in themes if raw[t]['s5'] <= r['s5']) / len(themes)
            accp = sum(1 for t in themes if (raw[t]['s5']/5 - (raw[t]['s10']-raw[t]['s5'])/5) <= (r['s5']/5 - (r['s10']-r['s5'])/5)) / len(themes)
            relp = sum(1 for t in themes if (raw[t]['r20'] if raw[t]['r20'] is not None else -9) <= (r['r20'] if r['r20'] is not None else -9)) / len(themes)
            etfp = sum(1 for t in themes if (raw[t]['etf'] if raw[t]['etf'] is not None else -9) <= (r['etf'] if r['etf'] is not None else -9)) / len(themes)
            sc[th] = W['mf5']*mf5p + W['rel']*relp + W['mf_accel']*accp + W['etf']*etfp
        ranked = sorted(themes, key=lambda t: -sc[t])
        g, cj = gate_pass('农业', d)
        ar = ranked.index('农业') + 1
        verdict = 'confirmed_candidate' if ar <= 2 and g >= 0.35 else ('watch' if ar <= 2 else ('reserve' if g >= 0.35 else 'none'))
        if firsts['rank1'] is None and ar == 1: firsts['rank1'] = d
        if firsts['confirmed'] is None and verdict == 'confirmed_candidate': firsts['confirmed'] = d
        rows.append({'date': d, 'agri_rank': ar, 'score': round(sc['农业'], 3), 'gate': round(g, 2), 'top1': ranked[0], 'top2': ranked[1], 'verdict': verdict})
    for r_ in rows:
        if r_['date'] in ('20260724', '20260803', '20260812', '20260818', '20260821', '20260824', '20260827', '20260901', '20260904', '20260908') or r_['agri_rank'] == 1:
            print(r_['date'], 'rank', r_['agri_rank'], 'score', r_['score'], 'gate', r_['gate'], 'top', r_['top1'][:6], r_['top2'][:6], r_['verdict'], flush=True)
    print('FIRSTS rank1:', firsts['rank1'], 'confirmed_candidate:', firsts['confirmed'], flush=True)
    json.dump({'firsts': firsts, 'rows': rows}, open(os.path.join(DATA, 'replay_agri_rank.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE /app/data/replay_agri_rank.json')

if __name__ == '__main__':
    main()
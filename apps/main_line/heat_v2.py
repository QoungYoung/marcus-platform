# -*- coding: utf-8 -*-
"""heat_v2.py — Wolf 化资金热度(2026-09-08, step2)
动机实证: fusion proxy 的 conc(净流入集中度) 与资金方向脱节(流出大户高分/真流入农业0.14),
        0.5权重主导致 '热度TOP2' 不可作 Wolf 资金主导代理。
v2 因子(全部方向经实证, percentile 0-1 于 15 主题间):
  mf5     : 主题成分 主力净流入(单股 moneyflow_dc net_amount) 近5日累计均值
  mf_accel: 近5日主力净日均 - 前5-10日主力净日均 (>0=加速)
  rel     : r20 涨幅(概念等权)
  fund5   : (备用) concept_hist net 近5日 percentile
剔 conc。权重 --calibrate 用 32 行 Wolf 标注标定(expect True: rank<=2 match, False: rank>=3 match)。
用法: python3 heat_v2.py --date 20260904 [--calibrate]
产物: /app/data/heat_v2_{date}.json (ranked+factors) + /app/data/heat_v2_params.json (calibrate时)
"""
import sys, os, json, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def percentile_rank(vals):
    """升序->percentile 0-1"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    rk = [0] * len(vals)
    for pos, i in enumerate(order): rk[i] = (pos + 1) / len(vals)
    return rk

def main():
    date8 = '20260904'
    calibrate = '--calibrate' in sys.argv
    for i, a in enumerate(sys.argv[1:]):
        if a == '--date': date8 = sys.argv[i + 2]
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEMES, load as fm_load, close_series, net_series
    import numpy as np, pandas as pd
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    hist = fm_load('concept_hist.json')
    hh = {v.get('name', ''): v for v in hist.values()}
    t = pd.Timestamp(date8[:4] + '-' + date8[4:6] + '-' + date8[6:])
    # 概念成分 member 全量(与 concept_long 同源)
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    th_members = {}
    for th, cons in THEME_CONCEPTS.items():
        ts = []
        for c in cons:
            for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,)):
                ts.append(r[0])
        th_members[th] = list(dict.fromkeys(ts))
    # ---- 交易日(0904 前 20 个) ----
    all_days = sorted({d for v in hist.values() for d in v.get('dates', []) if d <= date8})
    days20 = all_days[-20:]
    # ---- moneyflow_dc 逐日 20 天全市场 ----
    mf = {}
    for d in days20:
        try:
            df = pro.moneyflow_dc(trade_date=d, fields='ts_code,net_amount')
            if df is not None and not df.empty:
                mf[d] = {str(r['ts_code']): float(r['net_amount']) for _, r in df.iterrows()}
        except Exception as e:
            print('mf err', d, str(e)[:60], flush=True)
        time.sleep(0.15)
    print('moneyflow days', len(mf), days20[0], '-', days20[-1], flush=True)
    # ---- 主题因子原始值 ----
    fac = {th: {} for th in MAIN_THEMES}
    for th, members in th_members.items():
        if not members: continue
        # mf5: 近5日主力净和; mf10: 近10日; 加速 = mf5/5 - mf前5(第6-10日)/5
        s5 = [sum(mf[d].get(ts, 0.0) for ts in members if d in mf) for d in days20[-5:]]
        s10 = [sum(mf[d].get(ts, 0.0) for ts in members if d in mf) for d in days20[-10:]]
        fac[th]['mf5'] = sum(s5)
        fac[th]['mf10'] = sum(s10)
        fac[th]['mf_accel'] = (sum(s5) / 5.0) - (sum(s10) / 10.0) if s10 else 0.0
        # rel: 概念等权 r20(用 concept_hist close 与 concept_long 等权? 简化 concept_hist close_series)
        rs = []
        for c in THEME_CONCEPTS[th]:
            v = hh.get(c)
            if not v: continue
            ser = close_series(v).dropna()
            b = ser[ser.index <= t]
            if len(b) >= 21 and float(b.iloc[-21]) > 0:
                rs.append(float(b.iloc[-1]) / float(b.iloc[-21]) - 1)
        fac[th]['rel'] = float(np.mean(rs)) if rs else 0.0
        # fund5: concept net 近5日
        f5 = []
        for c in THEME_CONCEPTS[th]:
            v = hh.get(c)
            if not v: continue
            ns = net_series(v).dropna()
            s = ns[ns.index <= t]
            if len(s) >= 5: f5.append(float(s.iloc[-5:].sum()))
        fac[th]['fund5'] = float(np.mean(f5)) if f5 else 0.0
    # ---- percentile 与默认权重排名 ----
    themes = [th for th in MAIN_THEMES if th in fac and th in THEME_CONCEPTS]
    for k in ('mf5', 'mf_accel', 'rel', 'fund5'):
        rk = percentile_rank([fac[th].get(k, -1e18) for th in themes])
        for i, th in enumerate(themes): fac[th][k + '_p'] = rk[i]
    def score(th, w):
        return w['mf5'] * fac[th].get('mf5_p', 0.5) + w['rel'] * fac[th].get('rel_p', 0.5) + w['mf_accel'] * fac[th].get('mf_accel_p', 0.5)
    w0 = {'mf5': 0.4, 'rel': 0.3, 'mf_accel': 0.3}
    ranked = sorted(themes, key=lambda th: -score(th, w0))
    print('=== heat_v2 默认权重', w0, 'date', date8, '===', flush=True)
    for th in ranked:
        mf5_raw = fac[th].get('mf5', 0.0)
        print('%-12s mf5p=%.2f relp=%.2f accp=%.2f score=%.3f | 主力5日净额%+.3f亿 rel20=%.1f%%' % (
            th, fac[th].get('mf5_p', 0.5), fac[th].get('rel_p', 0.5), fac[th].get('mf_accel_p', 0.5), score(th, w0),
            mf5_raw / 1e8, fac[th].get('rel', 0.0) * 100), flush=True)
    out = {'date': date8, 'weights': w0, 'ranked': [{'theme': th, 'rank': i + 1, 'score': round(score(th, w0), 3),
             'factors': {k: fac[th].get(k) for k in ('mf5', 'mf5_p', 'rel', 'rel_p', 'mf_accel', 'mf_accel_p')}}
            for i, th in enumerate(ranked)], 'raw': fac}
    json.dump(out, open(os.path.join(DATA, 'heat_v2_' + date8 + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE /app/data/heat_v2_%s.json' % date8, flush=True)
    # ---- 标定 ----
    if calibrate:
        labels = fm_load('wolf_labels_v2.json')['mainline']
        from fusion_mainline import MAIN_THEME_OF
        rows = []
        for l in labels:
            mt = MAIN_THEME_OF.get(l.get('theme'))
            if mt is None or mt not in THEME_CONCEPTS: continue
            d8 = str(l['date']).replace('-', '')
            if d8 != date8: continue
            rows.append((mt, bool(l.get('expect'))))
        if not rows:
            # 跨 32 行逐一(各标注日不同窗口)——本版只标定单日; 简化为对可用标注日各跑? 注: 单日无标注则跳过
            print('无当日标注(标注离散), 单日标定需每日窗口, 跳过; 可用多日因子需 PIT', flush=True)
        else:
            print('当日标注', len(rows), flush=True)
    json.dump({'weights': w0, 'note': '默认权重待多日 PIT 标定'}, open(os.path.join(DATA, 'heat_v2_params.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()

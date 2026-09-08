# -*- coding: utf-8 -*-
"""mainline_gate.py — 主线确认门组合器(2026-09-08, Wolf自上而下漏斗 v1)
口径(经 gate 标定): 结构 GATE(B_only t=0.35, F1 0.902) 是"资格"不是"排名"。
  confirmed_candidate = 热度排名 <= TOP_N 且 结构 GATE PASS   (资金主导 + 主升结构 = 主线候选)
  watch              = 热度排名 <= TOP_N 但 GATE FAIL          (资金在但结构未确认: 复核/观察)
  reserve            = GATE PASS 但 排名 > TOP_N               (结构健康等资金点火: 预备主线)
热度排名 v0 = fusion 现 proxy(0.3fund+0.2rel+0.5conc), 注明待 step2 资金未跑升级。
用法: python3 mainline_gate.py --date 20260904 [--topn 2] [--fusion-json PATH(可选外部热度)]
"""
import sys, os, json
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def load_json(p, dflt=None):
    try: return json.load(open(p, encoding='utf-8'))
    except Exception: return dflt

def main():
    date8 = None; topn = 2; fusion_p = None
    for i, a in enumerate(sys.argv[1:]):
        if a == '--date' and i + 1 < len(sys.argv): date8 = sys.argv[i + 2]
        if a == '--topn': topn = int(sys.argv[i + 2])
        if a == '--fusion-json': fusion_p = sys.argv[i + 2]
    date8 = date8 or '20260904'
    # ---- 热度分 ----
    if fusion_p:
        fx = load_json(fusion_p)
        ranks = fx.get('ranked') if isinstance(fx, dict) else fx
    else:
        from fusion_mainline import get_signals, MAIN_THEMES, load as fm_load
        try:
            state = fm_load(f'main_line_state_{date8}.json')
        except Exception:
            state = {'catalyst': {}}
        hist = fm_load('concept_hist.json')
        d_h = date8[:4] + '-' + date8[4:6] + '-' + date8[6:]
        from fusion_mainline import theme_signals
        sig = theme_signals(hist, d_h, state)
        sc = {th: 0.3 * v.get('fund', 0.5) + 0.2 * v.get('rel', 0.5) + 0.5 * v.get('conc', 0.5)
              for th, v in sig.items()}
        rank_list = sorted(MAIN_THEMES, key=lambda k: -sc[k])
        ranks = [(th, round(sc[th], 3), i + 1) for i, th in enumerate(rank_list)]
    # ranks 归一: list of (theme, score, rank)
    if ranks and isinstance(ranks[0], dict):
        ranks = [(r['theme'], r.get('score'), r.get('rank')) for r in ranks]
    score_map = {t: s for t, s, _ in ranks}
    # ---- 结构 GATE ----
    tr = load_json(os.path.join(DATA, f'trend_confirm_{date8}_long.json'))
    if tr is None:
        print('缺少结构 json trend_confirm_%s_long.json, 先跑 trend_confirm --as-of %s' % (date8, date8))
        return
    gate_map = {}
    for t in tr.get('themes', []):
        gate_map[t['theme']] = {'gate': t.get('gate'), 'ratio': (t.get('track_b') or {}).get('ratio'),
                                'judgeable': (t.get('track_b') or {}).get('judgeable') or (t.get('track_b') or {}).get('total')}
    themes = list(dict.fromkeys([r[0] for r in ranks] + list(gate_map)))
    rows = []
    for th in themes:
        rk = next((r for r in ranks if r[0] == th), None)
        g = gate_map.get(th, {})
        rank = rk[2] if rk else 99
        score = rk[1] if rk else None
        gate = g.get('gate')
        if rank <= topn and gate:
            verdict = 'confirmed_candidate'
        elif rank <= topn and gate is False:
            verdict = 'watch'
        elif gate and rank > topn:
            verdict = 'reserve'
        else:
            verdict = 'none'
        rows.append({'theme': th, 'heat_rank': rank, 'heat_score': score,
                     'gate': gate, 'gate_ratio': g.get('ratio'), 'judgeable': g.get('judgeable'),
                     'verdict': verdict})
    rows.sort(key=lambda x: (x['heat_rank'] if x['heat_rank'] else 99, -(x['gate_ratio'] or 0)))
    print('热度排名 TOP%d(现 proxy, 待资金未跑升级) x 结构 GATE(B_only>=0.35) | date', date8, flush=True)
    for r in rows:
        print('%-12s rank=%-2s score=%-5s GATE=%-5s(%.2f/%d) -> %s' % (
            r['theme'], r['heat_rank'] if r['heat_rank'] < 99 else '-',
            r['heat_score'], ('PASS' if r['gate'] else 'FAIL') if r['gate'] is not None else 'n/a',
            r['gate_ratio'] or 0, r['judgeable'] or 0, r['verdict']), flush=True)
    out = {'date': date8, 'gate_rule': 'B_only>=0.35(F1 0.902 标定)',
           'heat_note': ('外部热度 json (heat_v2)' if fusion_p else 'v0 fusion proxy 0.3fund+0.2rel+0.5conc, 待step2资金未跑升级'),
           'topn': topn, 'rows': rows}
    p = os.path.join(DATA, f'mainline_gate_{date8}.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', p)
    try:
        from mainline_confirm_state import ensure_history
        n = ensure_history(rows, date8)
        if n: print('CONFIRM_HISTORY', date8, 'themes', n, flush=True)
    except Exception as e:
        print('confirm_history err', str(e)[:80])

if __name__ == '__main__':
    main()

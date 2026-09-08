# -*- coding: utf-8 -*-
"""trend_gate_calibrate.py — 双轨合并口径标定(2026-09-08, 审计后修复③)
背景: 之前标定只优化 A轨(主题指数), 名单却用 A+B 双轨+未标定 theme_pass_ratio=0.5 → 展示口径不可信。
本脚本固定已标定 A 轨参数(recency60/break0.98/swing3 等), 只标定对外判定:
  mode: A_only | B_only | A_or_B | A_and_B
  t   : B轨 概念confirmed比例阈值 0.30~0.70
判据: 32行 Wolf 标注(expect True 25 应 gate True; expect False 7 应 gate False), 指标 F1/rec/prec/hit/fp。
产物: /app/data/trend_gate_params.json
"""
import sys, os, json, itertools
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    from trend_confirm import judge_series, _load_params, TREND_CFG, load_by_name
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEME_OF
    cfg = _load_params(dict(TREND_CFG), os.path.join(DATA, 'trend_confirm_params.json'))
    hist = load_by_name(os.path.join(DATA, 'concept_long.json'))
    dates_all = max((v.get('dates') or []) for v in hist.values() if v.get('dates'))
    labels = json.load(open(os.path.join(DATA, 'wolf_labels_v2.json'), encoding='utf-8'))['mainline']
    rows = []
    for l in labels:
        mt = MAIN_THEME_OF.get(l.get('theme'))
        if mt is None or mt not in THEME_CONCEPTS: continue
        d8 = str(l['date']).replace('-', '')
        if d8 not in dates_all: continue
        rows.append({'date': d8, 'theme': mt, 'expect': bool(l.get('expect'))})
    usable = [r for r in rows if dates_all.index(r['date']) + 1 >= 120]
    print('usable', len(usable), 'True', sum(1 for r in usable if r['expect']), 'False', sum(1 for r in usable if not r['expect']), flush=True)
    # 缓存每 (theme,date): (A_confirmed, concept_confirmed/judgeable)
    def idx_series(theme, d8):
        vs = [hist[c] for c in THEME_CONCEPTS[theme] if c in hist]
        j = dates_all.index(d8)
        return theme_index_safe(vs, THEME_CONCEPTS[theme], j)
    def theme_index_safe(vs, cons, j):
        from trend_confirm import fill_series
        pcts = []
        for v in vs:
            cc = fill_series(v['close'][:j + 1])
            if cc is None or not cc: continue
            base = cc[0]
            if base <= 0: continue
            pcts.append([(x / base - 1.0) * 100.0 for x in cc])
        if not pcts: return None
        n = len(pcts[0])
        return [100.0 + sum(p[i] for p in pcts) / len(pcts) for i in range(n)]
    cache = {}
    for r in usable:
        th, d8 = r['theme'], r['date']
        if (th, d8) in cache: continue
        s = idx_series(th, d8)
        a_ok = bool(s is not None and len(s) >= 120 and judge_series(s, cfg).get('stage') == 'confirmed')
        cn = cj = 0
        for c in THEME_CONCEPTS[th]:
            v = hist.get(c)
            if not v: continue
            jj = dates_all.index(d8)
            cc = [x for x in v['close'][:jj + 1]]
            st = judge_series(cc, cfg).get('stage')
            if st in ('confirmed', 'suspect', 'not_confirmed'):
                cj += 1
                if st == 'confirmed': cn += 1
        cache[(th, d8)] = {'a': a_ok, 'ratio': (cn / cj if cj else 0.0), 'cn': cn, 'cj': cj}
    print('cache built', len(cache), flush=True)
    MODES = ['A_only', 'B_only', 'A_or_B', 'A_and_B']
    res = []
    for mode, t in itertools.product(MODES, [x / 100.0 for x in range(30, 75, 5)]):
        hit = fp = 0
        for r in usable:
            cc = cache[(r['theme'], r['date'])]
            a = cc['a']; b = cc['ratio'] >= t
            gate = {'A_only': a, 'B_only': b, 'A_or_B': a or b, 'A_and_B': a and b}[mode]
            if r['expect']:
                if gate: hit += 1
            else:
                if gate: fp += 1
        tn = sum(1 for r in usable if r['expect'])
        prec = hit / max(hit + fp, 1); rec = hit / max(tn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        res.append({'mode': mode, 't': t, 'hit': hit, 'fp': fp, 'prec': round(prec, 3), 'rec': round(rec, 3), 'f1': round(f1, 3)})
    best = max(res, key=lambda x: x['f1'])
    pl = [x for x in res if x['f1'] >= 0.98 * best['f1']]
    # 保守 chosen: 平原内 fp 最少, 再取 rec 高、B_only 优先(免主题指数合成争议)
    chosen = min(pl, key=lambda x: (x['fp'], -x['rec'], 0 if x['mode'] == 'B_only' else 1))
    print('chosen', chosen, flush=True)
    out = {'generator': 'trend_gate_calibrate_v1', 'date': '20260908', 'labels_used': len(usable),
           'a_params': {k: cfg[k] for k in ('new_high_window', 'confirm_recency_days', 'swing_k', 'break_ratio')},
           'best': best, 'chosen': chosen, 'plateau': pl, 'all': sorted(res, key=lambda x: -x['f1'])}
    json.dump(out, open(os.path.join(DATA, 'trend_gate_params.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for x in sorted(res, key=lambda y: -y['f1'])[:12]:
        print(x['mode'], 't=%.2f' % x['t'], '| f1', x['f1'], 'rec', x['rec'], 'prec', x['prec'], 'hit', x['hit'], 'fp', x['fp'], flush=True)
    print('WROTE /app/data/trend_gate_params.json')

if __name__ == '__main__':
    main()

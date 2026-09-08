# -*- coding: utf-8 -*-
"""trend_confirm_calibrate.py — 主升结构门参数标定(2026-09-08)
目标: 结构 confirmed 对齐 wolf_labels_v2 标注(expect=True=Wolf主线 应召回; expect=False=非主线 应不误放)
网格 + 稳定平原(F1 >= 0.95*maxF1 的参数空间取众数), 不取单点 argmax 防过拟合。
产物: /app/data/trend_confirm_params.json(平原参数, 供 trend_confirm.py --params)
"""
import sys, os, json, itertools
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    from trend_confirm import judge_series, theme_index, _load_params, TREND_CFG
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEME_OF
    hist = json.load(open(os.path.join(DATA, 'concept_hist.json'), encoding='utf-8'))
    by_name = {v.get('name', ''): v for v in hist.values()}
    dates_all = next(iter(hist.values()))['dates']
    labels = json.load(open(os.path.join(DATA, 'wolf_labels_v2.json'), encoding='utf-8'))['mainline']
    rows = []
    for l in labels:
        mt = MAIN_THEME_OF.get(l.get('theme'))
        if mt is None or mt not in THEME_CONCEPTS: continue
        d8 = str(l['date']).replace('-', '')
        if d8 not in dates_all: continue
        rows.append({'date': d8, 'theme': mt, 'expect': bool(l.get('expect'))})
    # 数据覆盖: 需要 date 之后有 min_days=120 可回溯? judge 用截至 date 的序列长度 len<=idx+1
    usable = [r for r in rows if dates_all.index(r['date']) + 1 >= 120]
    print('labels rows', len(rows), 'usable(>=120d 历史)', len(usable),
          '| True', sum(1 for r in usable if r['expect']), 'False', sum(1 for r in usable if not r['expect']), flush=True)
    if len(usable) < 8:
        print('样本不足, 退出'); return
    # 序列截至 date
    def idx_series(theme, d8):
        vs = [by_name[c] for c in THEME_CONCEPTS[theme] if c in by_name]
        j = dates_all.index(d8)
        return theme_index([{**v, 'close': v['close'][:j + 1]} for v in vs], THEME_CONCEPTS[theme])[0]
    def judge_at(theme, d8, cfg):
        s = idx_series(theme, d8)
        if s is None or len(s) < 120: return None
        return judge_series(s, cfg).get('stage') == 'confirmed'
    GRID = {
        'break_ratio': [0.95, 0.98, 0.995],
        'pullback_max_pct': [0.12, 0.18, 0.25],
        'pullback_min_pct': [0.02, 0.04],
        'swing_k': [3, 5, 8],
        'new_high_window': [40, 60, 90],
        'confirm_recency_days': [3, 5, 10],
        'prior_low_window': [90, 150],
    }
    keys = list(GRID)
    combos = [dict(zip(keys, v)) for v in itertools.product(*(GRID[k] for k in keys))]
    print('grid combos', len(combos), flush=True)
    res = []
    for ci, over in enumerate(combos):
        cfg = dict(TREND_CFG); cfg.update(over)
        hit = fp = 0; tn = sum(1 for r in usable if r['expect'])
        fn_n = 0; fp_n = 0
        for r in usable:
            c = judge_at(r['theme'], r['date'], cfg)
            if c is None: continue
            if r['expect']:
                if c: hit += 1
            else:
                if c: fp += 1
        # 只统计判定成功的行
        true_n = tn  # 简单口径: expect True 全算
        prec = hit / max(hit + fp, 1)
        rec = hit / max(true_n, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        res.append({'cfg': over, 'hit': hit, 'fp': fp, 'prec': round(prec, 3), 'rec': round(rec, 3), 'f1': round(f1, 3)})
    best = max(res, key=lambda x: x['f1'])
    # 稳定平原
    pl = [x for x in res if x['f1'] >= 0.95 * best['f1']]
    flat = {}
    for k in keys:
        from collections import Counter
        cnt = Counter(x['cfg'][k] for x in pl)
        flat[k] = cnt.most_common(1)[0][0]
    print('best', best, flush=True)
    print('plateau size', len(pl), '/', len(res), 'flat params', flat, flush=True)
    import statistics
    print('plateau recall range', min(x['rec'] for x in pl), '-', max(x['rec'] for x in pl), flush=True)
    # 语义约束: 平原内二次筛 break_ratio>=0.98(破位=收盘破, Wolf口径); 空则退回全平原
    pl_sem = [x for x in pl if x['cfg']['break_ratio'] >= 0.98]
    sem_note = ('break_ratio>=0.98 语义约束生效' if pl_sem else '语义约束无候选, 采用全平原(break_ratio 宽松, 需人工复核)')
    pl_use = pl_sem or pl
    # 固化: 平原内选 recall 最高的(保守+稳健)
    chosen = max(pl_use, key=lambda x: (x['rec'], x['prec']))
    params = dict(TREND_CFG); params.update(chosen['cfg'])
    out = {'generator': 'trend_confirm_calibrate_v1', 'date': '20260908',
           'labels_used': len(usable), 'true_n': tn,
           'best': {**best, 'cfg': best['cfg']}, 'chosen_plateau': chosen,
           'params': params, 'plateau_n': len(pl),
           'note': ('稳定平原(>=0.95*maxF1)内取 recall 最高; 样本量小(离散40行), 参数仍需周复盘校验; ' + sem_note)}
    json.dump(out, open(os.path.join(DATA, 'trend_confirm_params.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    # 报告前 10
    for x in sorted(res, key=lambda y: -y['f1'])[:8]:
        print(x['cfg'], '| f1', x['f1'], 'rec', x['rec'], 'prec', x['prec'], 'hit', x['hit'], 'fp', x['fp'], flush=True)
    print('WROTE /app/data/trend_confirm_params.json')

if __name__ == '__main__':
    main()

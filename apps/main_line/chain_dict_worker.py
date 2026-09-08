# -*- coding: utf-8 -*-
"""chain_dict_worker.py — 主题词典规则验证 helper(2026-09-08, 供 dsh subagent 批量自律循环)
naive 模式: THEME_CONCEPTS[theme] 全部概念单段 + kw=概念名直译 -> 基线
dict  模式: 给定词典 json {theme, segments:[{role,label,concepts,kw}], targets, impurity, rationale} -> 规则跑+指标
结果 json -> --out 或 /app/data/chain_dict_w_<theme>.json; stdout 打印 eval 摘要
"""
import sys, os, json, sqlite3, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')
VERIFY_MV_N = 20

def parse_args():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'naive'
    theme = sys.argv[2] if len(sys.argv) > 2 else '半导体/芯片'
    dict_p = None
    out_p = None
    if '--out' in sys.argv: out_p = sys.argv[sys.argv.index('--out') + 1]
    if mode == 'dict' and len(sys.argv) > 3 and not sys.argv[3].startswith('--'):
        dict_p = sys.argv[3]
    return mode, theme, dict_p, out_p

def main():
    import chain_map as cm
    from fusion_mainline import THEME_CONCEPTS
    from app.api.market import _get_tushare_pro
    mode, theme, dict_p, out_p = parse_args()
    pro = _get_tushare_pro()
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    names = dict(zip(sb['ts_code'], sb['name'])) if sb is not None else {}
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    # 概念存在性校验: 候选=概念表真实名
    alln = {r[0] for r in db.execute('SELECT DISTINCT concept_name FROM stock_concept_map')}
    if mode == 'naive':
        cons = THEME_CONCEPTS.get(theme, [])
        segs = [{'role': 'naive', 'label': theme + '·全概念(naive)', 'concepts': [c for c in cons if c in alln],
                 'kw': [c for c in cons if c in alln]}]
        targets, impurity, rationale = [], [], 'naive baseline'
    else:
        dd = json.load(open(dict_p, encoding='utf-8'))
        segs = dd.get('segments') or dd.get(theme, [])
        targets = dd.get('targets') or []
        impurity = dd.get('impurity') or []
        rationale = dd.get('rationale', '')
        segs = [s for s in segs if s.get('concepts')]
        for s in segs:
            s['concepts'] = [c for c in s['concepts'] if c in alln]
    mv, mv_date = cm.fetch_mv(pro, time.strftime('%Y%m%d'))
    res = []
    for seg in segs:
        codes, big, seen_c, seen_b = [], [], set(), set()
        audit = []
        for c in seg['concepts']:
            c12 = cm.concept_stocks(db, c, 12)
            cfull = cm.concept_stocks(db, c, 100)
            for ts in c12:
                if ts not in seen_c: seen_c.add(ts); codes.append(ts)
            for ts in cfull:
                if ts not in seen_b: seen_b.add(ts); big.append(ts)
            mt = sorted([t for t in cfull if t in mv], key=lambda t: -mv[t])[:4]
            audit.append({'concept': c, 'n': len(cfull), 'top': [names.get(t, '') for t in mt]})
        mv_sorted = sorted(big, key=lambda t: -mv.get(t, 0)) if mv else []
        pool = list(dict.fromkeys([t for t in mv_sorted[:VERIFY_MV_N] if mv.get(t)] + codes[:6]))
        vd = {}
        for ts in pool:
            vd[ts] = cm.fina_verdict(pro, ts, seg, names); time.sleep(0.08)
        ver = [vd[t] for t in pool if vd[t]['ok']]
        ver.sort(key=lambda v: -mv.get(v['ts'], 0))
        rej = sorted([vd[t] for t in pool if not vd[t]['ok']], key=lambda v: -mv.get(v['ts'], 0))
        res.append({'label': seg.get('label', seg.get('role', '')), 'concepts': seg['concepts'],
                    'audit': audit, 'leading': [v['name'] for v in ver[:3]],
                    'verified_all': [v['name'] for v in ver],
                    'rejected_top': [{'name': v['name'], 'mainbz': v['mainbz'][:2]} for v in rej[:8]]})
    va = set(); lead = set()
    for s in res: va.update(s['verified_all']); lead.update(s['leading'])
    out = {'theme': theme, 'mode': mode, 'date': time.strftime('%Y%m%d'),
           'mv_date': mv_date, 'dict_json': dict_p, 'rationale': rationale,
           'segments': res,
           'targets': targets, 'impurity': impurity,
           'metrics': {'target_hit': sum(1 for t in targets if t in va), 'target_total': len(targets),
                       'impurity_in_verified': [t for t in impurity if t in va],
                       'impurity_in_leading': [t for t in impurity if t in lead],
                       'verified_names': sorted(va)}}
    p = out_p or os.path.join(DATA, 'chain_dict_w_' + theme.replace('/', '_') + '.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    m = out['metrics']
    print('theme=%s mode=%s target %d/%d | impurity_v %s impurity_l %s' % (
        theme, mode, m['target_hit'], m['target_total'], m['impurity_in_verified'], m['impurity_in_leading']), flush=True)
    for s in res:
        print('  [%s] pool_verified=%d leading=%s' % (s['label'], len(s['verified_all']), s['leading']), flush=True)
        print('     rejected:', [r_['name'] for r_ in s['rejected_top'][:5]], flush=True)
    print('WROTE', p, flush=True)

if __name__ == '__main__':
    main()

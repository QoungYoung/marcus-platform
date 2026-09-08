# -*- coding: utf-8 -*-
"""trend_probe_inputs.py — trend_confirm 输入探测(2026-09-08)
对 fusion THEME_CONCEPTS(15主题) x concept_hist.json(521东财概念) 做名称匹配探测,
并报告日期滞后/序列长度/双轨指数可用性。产物 /app/data/trend_probe_inputs.json
"""
import sys, os, json
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    hist = json.load(open(os.path.join(DATA, 'concept_hist.json'), encoding='utf-8'))
    from fusion_mainline import THEME_CONCEPTS
    names = {v.get('name', ''): k for k, v in hist.items()}
    name_set = set(names)
    lens = [len(v.get('dates', [])) for v in hist.values()]
    dlens = sorted({len(v.get('dates', [])) for v in hist.values()})
    d0 = min((v.get('dates') or [''])[0] for v in hist.values())
    d1 = max((v.get('dates') or [''])[-1] for v in hist.values())
    print('concepts', len(hist), '| name-unique', len(name_set), '| date-lens', dlens,
          '| span', d0, '->', d1, flush=True)
    out = {'concepts': len(hist), 'span': [d0, d1], 'date_lens': sorted(dlens), 'themes': []}
    total_match = 0; total_c = 0
    for th, cons in THEME_CONCEPTS.items():
        exact, inc, miss = [], [], []
        for c in cons:
            if c in name_set:
                exact.append(c)
            elif any(c in n or n in c for n in name_set):
                inc.append((c, [n for n in name_set if c in n or n in c][:4]))
            else:
                miss.append(c)
        total_c += len(cons); total_match += len(exact) + len(inc)
        out['themes'].append({'theme': th, 'concepts_n': len(cons), 'exact': exact,
                              'contains': inc, 'missing': miss,
                              'match_rate': round((len(exact) + len(inc)) / max(len(cons), 1), 2)})
        print(f"[{th}] n={len(cons)} exact={len(exact)} contains={len(inc)} missing={len(miss)} "
              f"({len(miss) and miss})", flush=True)
    # 双轨可用性: 每主题 exact/contains 概念在 hist 是否都有完整 close
    print('total match', total_match, '/', total_c, flush=True)
    json.dump(out, open(os.path.join(DATA, 'trend_probe_inputs.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('WROTE', os.path.join(DATA, 'trend_probe_inputs.json'))

main()

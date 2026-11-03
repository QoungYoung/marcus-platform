#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_candidates_open_filter.py — T+1 09:25 集合竞价/开盘过滤（不交易）。

读 theme_candidates_merged.json，给每只候选打：
  pass / skip_chase / wait_pullback
高开 >3% 不追；低开且破前低 -> wait_pullback；其余 pass。
输出 theme_candidates_final.json。
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'backend')):
    if p not in sys.path: sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    a = ap.parse_args()
    _now = datetime.now()
    _basis = _now.strftime('%Y%m%d') if _now.hour >= 15 else (_now - timedelta(days=1)).strftime('%Y%m%d')
    try:
        from mainline_common import last_trade_date
        _d = last_trade_date(_basis)
    except Exception:
        _d = _basis
    d = a.date or _d
    base = os.path.join(ROOT, 'data', 'mainline', d)
    p = os.path.join(base, 'theme_candidates_merged.json')
    if not os.path.exists(p):
        print(json.dumps({'ok': False, 'error': 'merged missing', 'file': p}, ensure_ascii=False)); return 2
    data = json.load(open(p, encoding='utf-8'))
    from app.services.t_data_sources import fetch_quote_one
    rows = []
    for x in data.get('merged') or []:
        sym = str(x.get('symbol') or '').upper()
        q = fetch_quote_one(sym) or {}
        px = float(q.get('current') or 0)
        pre = float(q.get('pre_close') or 0)
        gap = (px / pre - 1) if pre > 0 and px > 0 else None
        verdict = 'no_quote'
        if gap is not None:
            if gap > 0.03:
                verdict = 'skip_chase'
            elif gap < -0.02:
                verdict = 'wait_pullback'
            else:
                verdict = 'pass'
        rows.append({**x, 'open_price': px or None, 'pre_close': pre or None,
                     'gap_pct': round(gap * 100, 2) if gap is not None else None,
                     'open_verdict': verdict})
    out = {'date': d, 'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'source': p, 'candidates': rows,
           'pass': [x for x in rows if x['open_verdict'] == 'pass'],
           'skip_chase': [x for x in rows if x['open_verdict'] == 'skip_chase'],
           'wait_pullback': [x for x in rows if x['open_verdict'] == 'wait_pullback']}
    q = os.path.join(base, 'theme_candidates_final.json')
    json.dump(out, open(q, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'date': d, 'total': len(rows), 'pass': len(out['pass']),
                      'skip_chase': len(out['skip_chase']), 'wait': len(out['wait_pullback']), 'file': q},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

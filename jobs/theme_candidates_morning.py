#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_candidates_morning.py — T+1 早盘 A/B 候选合并（不交易）。

T 日收盘后数据可能不齐；T+1 08:00 用上一交易日最终数据重跑/合并：
  A 强势通道（theme_sleeve_selected.json；缺失时可重跑 facts/select）
  B 底部首次放量通道（theme_b_candidates.py）
输出 data/mainline/<T>/theme_candidates_merged.json。
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'jobs'), os.path.join(ROOT, 'backend')):
    if p not in sys.path: sys.path.insert(0, p)
from core.tushare_relay import relay_query
from mainline_common import last_trade_date


def _run(args):
    p = subprocess.run([sys.executable] + args, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout or '')[-2000:], (p.stderr or '')[-1000:]


def _count(api, d):
    try:
        df = relay_query(api, trade_date=d, limit=6000)
        return 0 if df is None else len(df)
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='', help='T 日；默认上一交易日')
    ap.add_argument('--retry-a', action='store_true', help='A 缺失时重跑 facts/select')
    ap.add_argument('--max-members', type=int, default=12)
    a = ap.parse_args()
    t = a.date or last_trade_date(datetime.now().strftime('%Y%m%d'))
    base = os.path.join(ROOT, 'data', 'mainline', t)
    out = {'date': t, 'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'readiness': {}, 'channels': {'A': [], 'B': []}, 'merged': []}
    if not os.path.exists(os.path.join(base, 'theme.json')):
        out['error'] = 'theme.json missing: %s' % base
        print(json.dumps(out, ensure_ascii=False)); return 2
    out['readiness'] = {'daily': _count('daily', t), 'cyq_perf': _count('cyq_perf', t),
                        'limit_list_d': _count('limit_list_d', t)}
    out['readiness']['daily_ok'] = out['readiness']['daily'] >= 4000
    out['readiness']['a_data_ok'] = (out['readiness']['cyq_perf'] >= 3000
                                     and out['readiness']['limit_list_d'] >= 1)
    # B：严格规则，不依赖 cyq/limit
    if out['readiness']['daily_ok']:
        rc, so, se = _run(['jobs/theme_b_candidates.py', '--date', t, '--max-members', str(a.max_members)])
        out['b_run'] = {'rc': rc, 'stdout': so, 'stderr': se}
        bp = os.path.join(base, 'b_candidates.json')
        if os.path.exists(bp):
            b = json.load(open(bp, encoding='utf-8'))
            out['channels']['B'] = b.get('stocks') or []
            out['b_themes'] = b.get('themes') or []
    # A
    apath = os.path.join(base, 'theme_sleeve_selected.json')
    if not os.path.exists(apath) and a.retry_a and out['readiness']['a_data_ok']:
        _run(['jobs/theme_sleeve_facts.py', '--date', t])
        _run(['jobs/theme_sleeve_select.py', '--date', t])
    if os.path.exists(apath):
        try:
            sel = json.load(open(apath, encoding='utf-8')).get('selected') or []
            out['channels']['A'] = [dict(x, channel='A') for x in sel]
        except Exception as e:
            out['a_error'] = str(e)[:160]
    # merge
    merged = {}
    for ch in ('A', 'B'):
        for x in out['channels'][ch]:
            sym = x.get('symbol') or x.get('code')
            if not sym: continue
            key = str(sym).upper()
            row = merged.setdefault(key, {'symbol': sym, 'theme': x.get('theme_id') or x.get('theme'),
                                          'theme_name': x.get('theme_name') or x.get('name'),
                                          'channels': [], 'a': None, 'b': None})
            row['channels'].append(ch)
            if ch == 'A': row['a'] = x
            else: row['b'] = x
    out['merged'] = list(merged.values())
    out['partial'] = not (out['channels']['A'] and out['channels']['B'])
    p = os.path.join(base, 'theme_candidates_merged.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'date': t, 'readiness': out['readiness'], 'A': len(out['channels']['A']),
                      'B': len(out['channels']['B']), 'merged': len(out['merged']), 'partial': out['partial'],
                      'file': p}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

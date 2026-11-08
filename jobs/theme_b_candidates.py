#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_b_candidates.py — B 规则（低位+首次放量）候选，不交易。

第一版口径（阈值是代理，不是狼大原话）：
  题材：ret20 <= 0 且 amount_ratio >= 1.25
  个股：ret20 <= 0、距60日高 <= -10%、amount_ratio >= 1.25、
        盘中低点不低于前低 2%、当日涨幅 <= 6%
  flat_low_days 只记录，不强制（避免把“长期横盘”误判成硬门槛）。
输出 data/mainline/<date>/b_candidates.json。
"""
from __future__ import annotations
import argparse, json, os, statistics, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'jobs'), os.path.join(ROOT, 'backend')):
    if p not in sys.path: sys.path.insert(0, p)
import yaml
from stock_agent_facts import members_for, _fetch_bars_multi


def _f(v, d=0.0):
    try: return float(v)
    except Exception: return d


def _metrics(df):
    df = df.sort_values('trade_date')
    c = [float(x) for x in df['close']]
    h = [float(x) for x in df['high']]
    l = [float(x) for x in df['low']]
    a = [float(x) for x in df['amount']]
    p = [float(x or 0) for x in df['pct_chg']]
    if len(c) < 25 or c[-1] <= 0:
        return None
    px = c[-1]
    r20 = px / c[-21] - 1 if len(c) > 21 else 0
    hi60 = max(h[-60:]) if len(h) >= 60 else max(h)
    dist60 = px / hi60 - 1 if hi60 else 0
    lo20, hi20 = min(l[-20:]), max(h[-20:])
    mid = (lo20 + hi20) / 2
    flat = sum(1 for i in range(-20, 0) if (h[i] - l[i]) / c[i] < 0.02 and c[i] <= mid)
    box20 = (hi20 - lo20) / px if px else 0
    avg20 = statistics.mean(a[-21:-1]) if len(a) > 21 else statistics.mean(a[:-1])
    ar = a[-1] / avg20 if avg20 else 0
    base_ar = (statistics.mean(a[-6:-1]) / avg20) if len(a) > 6 and avg20 else 1
    prev_low = min(l[-21:-1]) if len(l) > 21 else min(l[:-1])
    return {'price': round(px, 2), 'ret20': round(r20 * 100, 2),
            'dist60': round(dist60 * 100, 2), 'flat': flat,
            'box20': round(box20 * 100, 2), 'amount_ratio': round(ar, 2),
            'base_ar': round(base_ar, 2), 'prev_low': round(prev_low, 2),
            'low': round(l[-1], 2), 'pct': round(p[-1], 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.now().strftime('%Y%m%d'))
    ap.add_argument('--max-members', type=int, default=8)
    ap.add_argument('--themes', default='')
    ap.add_argument('--max-base-ar', type=float, default=1.2, help='前5日均量/20日均量上限，识别首次放量')
    ap.add_argument('--min-flat', type=int, default=3, help='20日横盘天数下限')
    args = ap.parse_args()
    base = os.path.join(ROOT, 'data', 'mainline', args.date)
    theme = json.load(open(os.path.join(base, 'theme.json'), encoding='utf-8'))
    try:
        sys.path.insert(0, os.path.join(ROOT, "backend"))
        from app.services.theme_universe_store import load_theme_config
        cfg = load_theme_config() or {}
        if not cfg:
            print("[theme_b_candidates] ⚠ theme_definition 表为空/未命中，退回读 universe_theme.yaml",
                  file=sys.stderr)
    except Exception as e:
        print(f"[theme_b_candidates] ⚠ 从 PG 读 theme 配置失败，退回读 universe_theme.yaml: {e}",
              file=sys.stderr)
        cfg = {}
    if not cfg:
        cfg = yaml.safe_load(open(os.path.join(ROOT, 'config', 'mainline', 'generated', 'universe_theme.yaml'), encoding='utf-8'))
    tcfg = {x['id']: x for x in (cfg.get('themes') or []) + (cfg.get('satellite_themes') or [])}
    wanted = [x.strip() for x in args.themes.split(',') if x.strip()]
    themes = []
    for x in (theme.get('trial') or []) + (theme.get('watch') or []):
        tid = x.get('id')
        if not tid or (wanted and tid not in wanted):
            continue
        row = next((t for t in (theme.get('themes') or []) if t.get('id') == tid), {}) or {}
        ar = _f(row.get('amount_ratio'))
        r20 = _f(row.get('ret20'))
        if r20 > 0 or ar < 1.25:
            continue
        themes.append({'theme': tid, 'name': row.get('name') or x.get('name'),
                       'ret1': row.get('ret1'), 'ret5': row.get('ret5'), 'ret20': r20,
                       'breadth': row.get('breadth'), 'amount_ratio': ar,
                       'leader': row.get('leader') or x.get('leader'), 'verdict': row.get('verdict') or x.get('verdict')})
    themes.sort(key=lambda z: (-(z.get('amount_ratio') or 0), -(z.get('ret5') or 0)))
    stocks = []
    stocks_loose = []
    for th in themes:
        t = tcfg.get(th['theme']) or {}
        try:
            _mem = sorted(members_for(t, args.date))[:args.max_members]
            _extras = []
            try:
                _extras.append((t.get('leader') or {}).get('code'))
                _extras += [(x or {}).get('code') for x in (t.get('ladder') or [])]
            except Exception:
                pass
            ms = list(dict.fromkeys([x for x in (_mem + _extras) if x]))
        except Exception as e:
            print('[b] members fail', th['theme'], str(e)[:100], flush=True); continue
        bars = _fetch_bars_multi(ms, '20260601', args.date, args.date) if ms else {}
        for code in ms:
            df = bars.get(code)
            if df is None: continue
            m = _metrics(df)
            if not m: continue
            if m['ret20'] > 0 or m['dist60'] > -10 or m['amount_ratio'] < 1.25:
                continue
            if m['low'] < m['prev_low'] * 0.98 or m['pct'] > 6 or m['box20'] > 30:
                continue
            score = (m['amount_ratio'] + max(0, _f(th.get('ret5'))) * 10
                     + m['flat'] * 0.05 - abs(m['ret20']) * 0.1)
            row = {'theme': th['theme'], 'theme_name': th['name'], 'code': code,
                   **m, 'score': round(score, 2)}
            stocks_loose.append(row)
            if m['base_ar'] > args.max_base_ar or m['flat'] < args.min_flat:
                continue
            stocks.append(row)
    stocks.sort(key=lambda x: (-x['score'], -x['amount_ratio']))
    out = {'date': args.date, 'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'rules': {'theme': 'ret20<=0 & amount_ratio>=1.25',
                     'stock': 'ret20<=0 & dist60<=-10% & amount_ratio>=1.25 & low>=prev_low*0.98 & pct<=6 & box20<=30%',
                     'first_volume': 'base_ar<=%.2f & flat>=%d' % (args.max_base_ar, args.min_flat)},
           'themes': themes, 'stocks': stocks[:20], 'stocks_loose': stocks_loose[:20]}
    p = os.path.join(base, 'b_candidates.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

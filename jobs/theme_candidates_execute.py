#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_candidates_execute.py — 09:45 执行 B 候选（默认 dry-run）。

只执行 theme_candidates_final.json 中 open_verdict=pass 且带 B 通道的标的：
  · 每票 probe 仓位（默认总资产 5%）
  · 同票已有持仓则跳过，避免 A/B 重复买
  · 同一天同一票只执行一次（data/theme_b_exec_state.json）
  · 统一走 t_gateway.gateway_execute；不绕过网关
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'backend'), os.path.join(ROOT, 'jobs')):
    if p not in sys.path: sys.path.insert(0, p)
STATE = os.path.join(ROOT, 'data', 'theme_b_exec_state.json')


def _load_state():
    try: return json.load(open(STATE, encoding='utf-8'))
    except Exception: return {}


def _save_state(s):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(s, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass


def _has_position(account: str, symbol: str) -> bool:
    try:
        import psycopg2
        c = psycopg2.connect(os.environ['DATABASE_URL']); cur = c.cursor()
        cur.execute("SELECT 1 FROM paper_positions WHERE account_id=%s AND UPPER(symbol)=UPPER(%s) AND volume>0",
                    (account, symbol))
        ok = cur.fetchone() is not None
        c.close(); return ok
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    ap.add_argument('--account', default=os.getenv('T_MONITOR_ACCOUNT', 'stock'))
    ap.add_argument('--probe-pct', type=float, default=0.05)
    ap.add_argument('--max-positions', type=int, default=2)
    ap.add_argument('--execute', action='store_true')
    a = ap.parse_args()
    d = a.date or datetime.now().strftime('%Y%m%d')
    base = os.path.join(ROOT, 'data', 'mainline', d)
    p = os.path.join(base, 'theme_candidates_final.json')
    if not os.path.exists(p):
        print(json.dumps({'ok': False, 'error': 'final missing', 'file': p}, ensure_ascii=False)); return 2
    data = json.load(open(p, encoding='utf-8'))
    cands = [x for x in (data.get('pass') or []) if 'B' in (x.get('channels') or [])]
    if not cands:
        print(json.dumps({'ok': True, 'date': d, 'executed': 0, 'reason': 'no B pass candidates'}, ensure_ascii=False))
        return 0
    os.environ.setdefault('WOLF_BUILD_CAP_ANCHOR', '1')
    from app.services.t_gateway import gateway_execute, t_net_asset
    from app.services.t_data_sources import fetch_quote_one
    nav = float(t_net_asset(a.account) or 0)
    state = _load_state()
    day = state.setdefault(d, {})
    done = []
    for x in cands[:a.max_positions]:
        sym = str(x.get('symbol') or '').upper()
        if not sym or day.get(sym):
            continue
        if _has_position(a.account, sym):
            day[sym] = {'skip': 'already_position'}; done.append({'symbol': sym, 'skip': 'already_position'}); continue
        q = fetch_quote_one(sym) or {}
        px = float(q.get('current') or 0)
        if px <= 0:
            day[sym] = {'skip': 'no_quote'}; done.append({'symbol': sym, 'skip': 'no_quote'}); continue
        vol = int((nav * a.probe_pct) / px / 100) * 100
        if vol < 100:
            day[sym] = {'skip': 'size_lt_100'}; done.append({'symbol': sym, 'skip': 'size_lt_100'}); continue
        if not a.execute:
            done.append({'symbol': sym, 'dry_run': True, 'price': px, 'volume': vol})
            continue
        gw = gateway_execute(sym, 'buy', px, vol, kind='theme_b_probe',
                             reason='theme_b:probe %.0f%%' % (a.probe_pct * 100),
                             decision_source='ai_led', account_id=a.account)
        day[sym] = {'price': px, 'volume': vol, 'gateway': gw, 't': datetime.now().isoformat()}
        done.append({'symbol': sym, 'price': px, 'volume': vol, 'gateway': gw})
    _save_state(state)
    print(json.dumps({'ok': True, 'date': d, 'nav': nav, 'execute': a.execute, 'results': done}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

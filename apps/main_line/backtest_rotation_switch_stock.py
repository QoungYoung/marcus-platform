# -*- coding: utf-8 -*-
"""backtest_rotation_switch_stock.py — 主线内切换下沉个股级 v0（先验证 E10 2026-03-19 海外链→国算）

输入(事件日 as_of)：
  - 卖出侧(旧链A)：qfq HIGH/结构破位 + 5日主力净流出
  - 买入侧(新链B)：position LOW/MID 或 vs1y有空间 + 5日主力净流入/不流出 + 非 PIT 公募核心拥挤(高)
输出: data/switch_stock_backtest.json + stdout（逐股 + 事件判定）
说明：E10 sell=buy_ai_hard(3) buy=buy_guosuan(5)，wave=defense 4-1 内部切换(B3 语义：防御期只做主线内未出货链调仓)。
"""
import os, sys, json, time, threading
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    import position_class as pc
except Exception as e:
    print("WARN position_class import:", e); pc = None

TOKEN = os.getenv('TUSHARE_TOKEN', '')
URL = os.getenv('TUSHARE_API_URL', '')
DATA = os.environ.get('DATA_DIR', 'data')

def _ts(sym):
    s = str(sym).strip().upper()
    if '.' in s:
        return s
    return sym + ('.SH' if sym.startswith(('6', '9', '5')) else '.SZ')

def call(api, params, fields):
    import urllib.request, gzip, json as _j
    body = {'api_name': api, 'token': TOKEN, 'params': params, 'fields': fields}
    req = urllib.request.Request(URL, data=_j.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    try:
        d = _j.loads(raw.decode())
    except UnicodeDecodeError:
        d = _j.loads(gzip.decompress(raw).decode())
    if d.get('code') != 0:
        raise RuntimeError(str(d.get('msg'))[:120])
    return d.get('data', {}).get('items') or []

_lock = threading.Lock(); _last = 0.0
def _throttle():
    global _last
    with _lock:
        wait = 0.35 - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()

_KL = {}
def kline(sym):
    if sym in _KL:
        return _KL[sym]
    _throttle()
    dl = call('daily', {'ts_code': _ts(sym), 'start_date': '20250101', 'end_date': '20260901'}, 'ts_code,trade_date,close')
    _throttle()
    af = call('adj_factor', {'ts_code': _ts(sym), 'start_date': '20250101', 'end_date': '20260901'}, 'ts_code,trade_date,adj_factor')
    adj = {str(r[1]): float(r[2]) for r in af}
    rows = []
    for r in dl:
        d = str(r[1]); a = adj.get(d)
        if a is None:
            continue
        rows.append((d, float(r[2]) * a))
    rows.sort()
    _KL[sym] = rows
    return rows

def series_at(sym, upto='20260319'):
    rows = [x for x in kline(sym) if x[0] <= upto]
    if len(rows) < 260:
        return None
    import pandas as pd
    idx = pd.to_datetime([x[0] for x in rows], format='%Y%m%d')
    return pd.Series([x[1] for x in rows], index=idx)

def stock_feat(sym, upto):
    ser = series_at(sym, upto)
    if ser is None or pc is None:
        return None, None
    try:
        f = pc.position_features(ser)
        if not f:
            return None, None
        st = pc.structure_of(ser)
        cls = pc.classify(f)
        return cls['position'], {**f, 'structure': st.get('desc', 'flat')}
    except Exception as e:
        print('feat err', sym, str(e)[:80])
        return None, None

_MF = {}
def moneyflow_sym(sym):
    if sym in _MF:
        return _MF[sym]
    _throttle()
    rows = call('moneyflow', {'ts_code': _ts(sym), 'start_date': '20260101', 'end_date': '20260901'},
                'ts_code,trade_date,net_mf_amount')
    out = {str(r[1]): float(r[2]) if r[2] is not None else 0.0 for r in rows}
    _MF[sym] = out
    return out

def mf5(sym, upto):
    m = moneyflow_sym(sym)
    days = sorted([d for d in m if d <= upto])[-5:]
    return sum(m[d] for d in days) if days else None

def crowd_pit(date):
    try:
        return json.load(open(os.path.join(DATA, 'crowding_pit', 'stock_crowd_%s.json' % date), encoding='utf-8')).get('stock') or {}
    except Exception:
        return {}

def main():
    d = '2026-03-19'; upto = '20260319'
    ev = json.load(open(os.path.join(DATA, 'wolf_tech_entries_stockmap.json'), encoding='utf-8'))['E10']
    sell = ev['baskets']['sell_ai_hard']; buy = ev['baskets']['buy_guosuan']
    pit = crowd_pit(d)
    # 链级决策：旧链(光通信)在当日 PIT 象限是否拥挤无空间(到出货周期) → 卖旧链；新链按个股选股
    try:
        qh = json.load(open(os.path.join(DATA, 'rotation_quadrant_history_pit.json'), encoding='utf-8'))['history']['20260319']
        chain_crowded = (qh.get('光通信') or {}).get('quadrant') in ('拥挤无空间', '拥挤但有空间')
    except Exception:
        chain_crowded = True
    rows = []
    for sym in sell:
        pos, f = stock_feat(sym, upto)
        m5 = mf5(sym, upto)
        p = pit.get(sym) or {}
        crowded_core = int(p.get('n_funds') or 0) >= 4 and float(p.get('sum_float') or 0) >= 1.0
        sell_ok = bool(chain_crowded)  # 链级“已到出货周期”是卖出主因；个股只记录状态
        rows.append({'side': 'sell', 'event': 'E10', 'symbol': sym, 'position': pos,
                     'vs1y': round(f.get('vs_1y_high_pct'), 1) if f else None,
                     'structure': (f or {}).get('structure'), 'mf5_yi': round(m5 / 1e4, 1) if m5 is not None else None,
                     'n_funds': int(p.get('n_funds') or 0), 'float_pct': float(p.get('sum_float') or 0),
                     'ok': bool(sell_ok)})
        print('SELL', sym, pos, (f or {}).get('structure'), 'mf5', rows[-1]['mf5_yi'], 'chain', chain_crowded, flush=True)
    for sym in buy:
        pos, f = stock_feat(sym, upto)
        m5 = mf5(sym, upto)
        p = pit.get(sym) or {}
        crowded_core = int(p.get('n_funds') or 0) >= 4 and float(p.get('sum_float') or 0) >= 1.0
        # 买侧只做“选股”：不拥挤 + 位置非极端HIGH；短期资金流出不拦(切换期常逆势买入)
        buy_ok = bool(f and pos in ('LOW', 'MID') and not crowded_core)
        rows.append({'side': 'buy', 'event': 'E10', 'symbol': sym, 'position': pos,
                     'vs1y': round(f.get('vs_1y_high_pct'), 1) if f else None,
                     'structure': (f or {}).get('structure'), 'mf5_yi': round(m5 / 1e4, 1) if m5 is not None else None,
                     'n_funds': int(p.get('n_funds') or 0), 'float_pct': float(p.get('sum_float') or 0),
                     'ok': bool(buy_ok)})
        print('BUY ', sym, pos, 'mf5', rows[-1]['mf5_yi'], 'crowd', crowded_core, 'ok', buy_ok, flush=True)
    sell_n = len(sell); buy_n = len(buy)
    sell_ok_n = sell_n if chain_crowded else 0
    buy_ok_n = sum(1 for r in rows if r['side'] == 'buy' and r['ok'])
    executed = bool(chain_crowded and buy_ok_n >= max(1, (buy_n + 1) // 2))
    out = {'event': 'E10', 'date': d, 'wave': 'defense/4-1', 'b3_semantics': '主线内海外链→国算',
           'sell_ok': '%d/%d' % (sell_ok_n, sell_n), 'buy_ok': '%d/%d' % (buy_ok_n, buy_n),
           'switch_executed': executed, 'rows': rows}
    path = os.path.join(DATA, 'switch_stock_backtest.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', path, 'sell_ok', out['sell_ok'], 'buy_ok', out['buy_ok'], 'executed', executed, flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

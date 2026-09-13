# -*- coding: utf-8 -*-
"""backtest_rotation_switch_stock.py — 主线内切换下沉个股级 v0.2（E09/E10/E11/E12）

模型（E10 验证后的分层语义）：
  1) 链级卖出决策：旧链 PIT 象限“拥挤/出货周期”→ 整链卖；象限不拥挤时启用股票级兜底
     (HIGH 或 5日主力净流出)——E12 光通信在 05-22 象限中性，Wolf 仍因“光仓高位降 45→18”卖出。
  2) 个股级买入选股：已定新链内 位置 LOW/MID + 非公募核心拥挤(n<4 或 float<1%)。
输出: data/switch_stock_backtest.json {events:[...], summary}
"""
import os, sys, json, time, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    import position_class as pc
except Exception as e:
    print("WARN position_class:", e); pc = None

# 2026-09-13: 取数走 core/tushare_relay.py（datahubco+promax），旧 TUSHARE_API_URL 已废弃
DATA = os.environ.get('DATA_DIR', 'data')
REL_DAYS = int(os.environ.get('SWITCH_REL_DAYS', '60'))

SWITCHES = [
    dict(eid='E09', date='2026-02-28', upto='20260227', sell_basket=None, buy_basket='packaging_low',
         sell_proxy=['688981.SH', '688041.SH', '002371.SZ', '603501.SH', '688012.SH'],
         sell_chain='芯片/半导体', note='高位科技→封测/低位(代理卖出=半导高位链)'),
    dict(eid='E10', date='2026-03-19', upto='20260319', sell_basket='sell_ai_hard', buy_basket='buy_guosuan',
         sell_proxy=[], sell_chain='光通信', note='海外链(麦米/CPO)→国算电源'),
    dict(eid='E11', date='2026-04-22', upto='20260422', sell_basket=None, buy_basket='materials',
         sell_proxy=['688012.SH', '688072.SH', '002371.SZ', '688120.SH'],
         sell_chain='芯片/半导体', note='半导体设备→材料(设备已新高)'),
    dict(eid='E12', date='2026-06-05', upto='20260605', sell_basket='sell_optics', buy_basket='buy_semi',
         sell_proxy=[], sell_chain='光通信', note='光45→18%内, 慢慢低吸半导体', fallback_stock_sell=True),
]

def _ts(sym):
    s = str(sym).strip().upper()
    if '.' in s:
        return s
    return sym + ('.SH' if sym.startswith(('6', '9', '5')) else '.SZ')

def _relay():
    """加载 core/tushare_relay.py（2026-09-13 起 datahubco 基础接口 + promax 聚合接口，
    替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib, sys
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for _p in pathlib.Path(__file__).resolve().parents:
        if (_p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(_p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def call(api, params, fields=""):
    """Tushare 中继查询（返回 items 行列表；中继内部含重试/分页/双源降级）。"""
    _fields, items = _relay().relay_items(api, fields=fields, **(params or {}))
    return items or []
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

def series_at(sym, upto):
    rows = [x for x in kline(sym) if x[0] <= upto]
    if len(rows) < 260:
        return None
    import pandas as pd
    return pd.Series([x[1] for x in rows], index=pd.to_datetime([x[0] for x in rows], format='%Y%m%d'))

def r60(sym, upto, n=60):
    rows = [x for x in kline(sym) if x[0] <= upto]
    if len(rows) < n + 1:
        return None
    return rows[-1][1] / rows[-(n + 1)][1] - 1.0

def stock_feat(sym, upto):
    ser = series_at(sym, upto)
    if ser is None or pc is None:
        return None, None
    try:
        f = pc.position_features(ser)
        if not f:
            return None, None
        st = pc.structure_of(ser)
        return pc.classify(f)['position'], {**f, 'structure': st.get('desc', 'flat')}
    except Exception:
        return None, None

_MF = {}
def moneyflow_sym(sym):
    if sym in _MF:
        return _MF[sym]
    _throttle()
    rows = call('moneyflow', {'ts_code': _ts(sym), 'start_date': '20260101', 'end_date': '20260901'},
                'ts_code,trade_date,net_mf_amount')
    _MF[sym] = {str(r[1]): float(r[2]) if r[2] is not None else 0.0 for r in rows}
    return _MF[sym]

def mf5(sym, upto):
    m = moneyflow_sym(sym)
    days = sorted([d for d in m if d <= upto])[-5:]
    return sum(m[d] for d in days) if days else None

def pit_stock(date):
    try:
        return json.load(open(os.path.join(DATA, 'crowding_pit', 'stock_crowd_%s.json' % date), encoding='utf-8')).get('stock') or {}
    except Exception:
        return {}

def nearest_quadrant(chain, upto):
    try:
        q = json.load(open(os.path.join(DATA, 'rotation_quadrant_history_pit.json'), encoding='utf-8'))
        ds = sorted([x for x in q['dates'] if x <= upto])
        if not ds:
            return None
        return (q['history'].get(ds[-1]) or {}).get(chain) or {}
    except Exception:
        return {}

def main():
    evmap = json.load(open(os.path.join(DATA, 'wolf_tech_entries_stockmap.json'), encoding='utf-8'))
    events_out = []
    for cfg0 in SWITCHES:
        eid = cfg0['eid']; d = cfg0['date']; upto = cfg0['upto']
        ev = evmap.get(eid) or {}
        sell = list(cfg0.get('sell_proxy') or [])
        if cfg0.get('sell_basket'):
            sell += list((ev.get('baskets') or {}).get(cfg0['sell_basket']) or [])
        buy = list((ev.get('baskets') or {}).get(cfg0['buy_basket']) or [])
        pit = pit_stock(d)
        quad = nearest_quadrant(cfg0['sell_chain'], upto)
        chain_crowded = bool(quad.get('quadrant') in ('拥挤无空间', '拥挤但有空间'))
        rows = []
        for sym in sell:
            pos, f = stock_feat(sym, upto)
            m5 = mf5(sym, upto)
            p = pit.get(sym) or {}
            stock_sell = bool(pos in ('HIGH', 'MID') and (m5 is not None and m5 < 0)) or pos in ('HIGH',) or (f or {}).get('structure') in ('双头M顶', '新高回落', '破位C杀')
            ok = bool(chain_crowded or (cfg0.get('fallback_stock_sell') and stock_sell))
            rows.append({'side': 'sell', 'event': eid, 'symbol': sym, 'position': pos,
                         'structure': (f or {}).get('structure'),
                         'mf5_yi': round(m5 / 1e4, 1) if m5 is not None else None,
                         'n_funds': int(p.get('n_funds') or 0), 'ok': ok})
            print(eid, 'SELL', sym, pos, rows[-1]['mf5_yi'], 'chain', chain_crowded, 'ok', ok, flush=True)
        peer = sorted(set(sell + buy))
        rr = {x: r60(x, upto, REL_DAYS) for x in peer}
        rv = [v for v in rr.values() if v is not None]
        med = sorted(rv)[len(rv) // 2] if rv else None
        for sym in buy:
            pos, f = stock_feat(sym, upto)
            m5 = mf5(sym, upto)
            p = pit.get(sym) or {}
            crowded = int(p.get('n_funds') or 0) >= 4 and float(p.get('sum_float') or 0) >= 1.0
            rel = bool(rr.get(sym) is not None and med is not None and rr[sym] <= med)
            ok = bool(f and not crowded and (pos in ('LOW', 'MID') or rel))
            rows.append({'side': 'buy', 'event': eid, 'symbol': sym, 'position': pos,
                         'rel_r60': round(rr.get(sym), 3) if rr.get(sym) is not None else None,
                         'peer_median_r60': round(med, 3) if med is not None else None,
                         'mf5_yi': round(m5 / 1e4, 1) if m5 is not None else None,
                         'n_funds': int(p.get('n_funds') or 0), 'ok': ok})
            print(eid, 'BUY ', sym, pos, rows[-1]['mf5_yi'], 'crowd', crowded, 'ok', ok, flush=True)
        sell_ok = sum(1 for r in rows if r['side'] == 'sell' and r['ok'])
        buy_ok = sum(1 for r in rows if r['side'] == 'buy' and r['ok'])
        sell_n = len(sell); buy_n = len(buy)
        executed = bool(sell_n and buy_n and sell_ok >= max(1, (sell_n + 1) // 2) and buy_ok >= max(1, (buy_n + 1) // 2))
        events_out.append({'event': eid, 'date': d, 'upto': upto, 'sell_chain': cfg0['sell_chain'],
                           'chain_crowded': chain_crowded, 'sell_ok': '%d/%d' % (sell_ok, sell_n),
                           'buy_ok': '%d/%d' % (buy_ok, buy_n), 'switch_executed': executed,
                           'note': cfg0['note'], 'rows': rows})
        print(eid, 'RESULT sell', sell_ok, '/', sell_n, 'buy', buy_ok, '/', buy_n, 'executed', executed, flush=True)
    path = os.path.join(DATA, 'switch_stock_backtest.json')
    json.dump({'method': '主线内切换个股级 v0.2 (E09-E12)', 'events': events_out},
              open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', path, flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""backtest_wolf_trend_refill.py — Wolf 买点对齐·个股“站上20日趋势线回补”触发 v0

目标事件: E05(液冷加仓) E06(AI硬回补) E08(半导体低吸) E12(光→半导体低吸) E13(国算加仓) 的 24 只代理股
（与 wolf_dip254_backtest 同一股票篮子，便于对照 254 盘口触发 vs 趋势线回补触发）。

触发定义(日线近似“站稳趋势线才加”, E05 语料):
  TRIGGER = 当日 close > MA20 且 MA20 > MA60（多头未死）
           且 前一日 close <= 前一日 MA20（由下方重新站上/突破）→ 次日不回落到 MA20 下视为确认，
           v0 直接以该日作为“站上日”，收益口径 = 站上日收盘买入的 T+5。
输出: data/trend_refill_backtest.json(逐行) + stdout 汇总
注意: 这是日线代理，非 5min/分时；Proxy 股≠Wolf 实盘持仓；n 小。
"""
import os, sys, json, time, threading
from datetime import date, timedelta

# 2026-09-13: 取数走 core/tushare_relay.py（datahubco+promax），旧 TUSHARE_API_URL 已废弃
DATA = os.environ.get('DATA_DIR', 'data')
END = '20260901'

def _to_ts(sym):
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
_lock = threading.Lock()
_last = 0.0
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
    ts = _to_ts(sym)
    _throttle()
    dl = call('daily', {'ts_code': ts, 'start_date': '20240101', 'end_date': END}, 'ts_code,trade_date,close')
    _throttle()
    af = call('adj_factor', {'ts_code': ts, 'start_date': '20240101', 'end_date': END}, 'ts_code,trade_date,adj_factor')
    adj = {str(r[1]): float(r[2]) for r in af}
    out = []
    for r in dl:
        d = str(r[1])
        a = adj.get(d)
        if a is None:
            continue
        out.append({'date': d, 'close': float(r[2]) * a})
    out.sort(key=lambda x: x['date'])
    _KL[sym] = out
    return out

def fwd_close(sym, upto, n=5):
    rows = [r for r in kline(sym) if r['date'] > upto]
    if len(rows) < n:
        return None
    return rows[n - 1]['close']

def price_at(sym, d):
    rows = [r for r in kline(sym) if r['date'] <= d]
    return rows[-1]['close'] if rows else None

def ma(closes, n):
    return sum(closes[-n:]) / n

def find_trigger(sym, wolf_yyyyMMdd, window_days=15):
    """wolf_yyyyMMdd 前后 window_days 个自然日窗口内找第一个站上20日线触发日。
    返回 (trigger_date or None, offset_trading, first_trigger_date 不限窗口)"""
    rows = kline(sym)
    idx = {r['date']: i for i, r in enumerate(rows)}
    wd = wolf_yyyyMMdd
    lo = (date.fromisoformat(wd[:4] + '-' + wd[4:6] + '-' + wd[6:]) - timedelta(days=window_days + 20)).strftime('%Y%m%d')
    hi = (date.fromisoformat(wd[:4] + '-' + wd[4:6] + '-' + wd[6:]) + timedelta(days=window_days + 20)).strftime('%Y%m%d')
    dates = [r['date'] for r in rows if lo <= r['date'] <= hi]
    found = []
    for i in range(1, len(dates)):
        d = dates[i]
        j = idx[d]
        if j < 60:
            continue
        closes = [r['close'] for r in rows[:j + 1]]
        c, pm = closes[-1], ma(closes[:-1], 20)
        ma20, ma60 = ma(closes, 20), ma(closes, 60)
        prev_close, prev_ma20 = closes[-2], pm
        if c > ma20 and ma20 > ma60 and prev_close <= prev_ma20:
            found.append(d)
    first_all = found[0] if found else None
    in_window = [d for d in found if d >= lo and d <= hi]
    trig = in_window[0] if in_window else None
    if trig is None:
        return None, None, first_all
    # offset: trading-day distance between wolf date and trigger
    wpos = idx.get(wd)
    tpos = idx.get(trig)
    if wpos is None or tpos is None:
        return trig, None, first_all
    return trig, tpos - wpos, first_all

EVENTS = [
    ('E05', '20251209', '液冷加仓', ['002837.SZ', '301018.SZ', '300499.SZ']),
    ('E06', '20251231', 'AI硬回补', ['300308.SZ', '300502.SZ', '300394.SZ']),
    ('E08', '20260114', '半导体低吸', ['688981.SH', '688041.SH', '002371.SZ', '603501.SH', '688012.SH', '600584.SH']),
    ('E12', '20260605', '光→半导体低吸', ['688012.SH', '688072.SH', '002371.SZ', '300054.SZ', '300236.SZ', '600584.SH']),
    ('E13', '20260708', '国算加仓', ['601138.SH', '000977.SZ', '000938.SZ', '603019.SH', '000034.SZ', '688041.SH']),
]

def main():
    rows = []
    for eid, wd, phase, syms in EVENTS:
        for sym in syms:
            trig, offset, first_all = find_trigger(sym, wd)
            wolf_close = price_at(sym, wd)
            wolf_f5 = fwd_close(sym, wd, 5)
            sys_f5 = fwd_close(sym, trig, 5) if trig else None
            rows.append({
                'event': eid, 'phase': phase, 'wolf_date': wd, 'symbol': sym,
                'trigger_date': trig, 'first_trigger_all': first_all,
                'offset_days': offset,
                'aligned_same_day': bool(trig == wd),
                'within3': bool(offset is not None and abs(offset) <= 3),
                'within5': bool(offset is not None and abs(offset) <= 5),
                'wolf_close': wolf_close, 'wolf_T5': wolf_f5,
                'sys_T5': sys_f5,
            })
            print(eid, sym, 'wolf', wd, 'trig', trig or '-', 'offset', offset, flush=True)
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, 'trend_refill_backtest.json')
    json.dump(rows, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', path, 'rows', len(rows), flush=True)
    same = sum(1 for r in rows if r['aligned_same_day'])
    w3 = sum(1 for r in rows if r['within3'])
    w5 = sum(1 for r in rows if r['within5'])
    trig_n = sum(1 for r in rows if r['trigger_date'])
    print('SUMMARY same_day=%d/24 within3=%d/24 within5=%d/24 triggered=%d' % (same, w3, w5, trig_n))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

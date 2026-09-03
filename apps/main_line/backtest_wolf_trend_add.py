# -*- coding: utf-8 -*-
"""backtest_wolf_trend_add.py — Wolf 买点对齐·趋势内加仓触发 v0（E05/E13 型，独立脚本）

背景：trend_refill(站上MA20回补) 只覆盖 E12 式破位低吸；E05/E13 是“趋势内继续加仓”——
价格始终在 MA20 上方，Wolf 在缩量回踩 MA20 附近企稳后加仓（语料：站稳趋势线上才加/继续加仓国产算力）。

触发 v0（日线，全量样本 24 只代理股 E05/E06/E08/E12/E13）：
  i>=75（MA60 可用）
  且 close > MA20 > MA60（上升趋势完好）
  且 前 4 日收盘都 > MA20*0.97（趋势内，非刚站回）
  且 昨收 <= MA20*1.02（回踩到 MA20 附近）
  且 今日收盘 > 昨收 且 >= MA20（企稳回升）
  且 今日量 <= 前5日均量*1.3（缩量企稳，非放量突破/恐慌）
输出: data/trend_add_backtest.json（逐行）+ stdout 汇总
"""
import os, sys, json, time, threading
from datetime import date, timedelta

TOKEN = os.getenv('TUSHARE_TOKEN', '')
URL = os.getenv('TUSHARE_API_URL', '')
DATA = os.environ.get('DATA_DIR', 'data')
END = '20260901'

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
    dl = call('daily', {'ts_code': _ts(sym), 'start_date': '20240101', 'end_date': END},
              'ts_code,trade_date,close,vol')
    _throttle()
    af = call('adj_factor', {'ts_code': _ts(sym), 'start_date': '20240101', 'end_date': END},
              'ts_code,trade_date,adj_factor')
    adj = {str(r[1]): float(r[2]) for r in af}
    out = []
    for r in dl:
        d = str(r[1]); a = adj.get(d)
        if a is None:
            continue
        out.append({'date': d, 'close': float(r[2]) * a,
                    'vol': float(r[3]) if len(r) > 3 and r[3] is not None else 0.0})
    out.sort(key=lambda x: x['date'])
    _KL[sym] = out
    return out

def ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None

def fwd_close(sym, upto, n=5):
    rows = [r for r in kline(sym) if r['date'] > upto]
    if len(rows) < n:
        return None
    return rows[n - 1]['close']

def price_at(sym, d):
    rows = [r for r in kline(sym) if r['date'] <= d]
    return rows[-1]['close'] if rows else None

def find_trend_add(sym, wd, window_days=15):
    rows = kline(sym)
    dates = [r['date'] for r in rows]
    idx = {d: i for i, d in enumerate(dates)}
    w = date.fromisoformat(wd[:4] + '-' + wd[4:6] + '-' + wd[6:])
    lo = (w - timedelta(days=window_days + 30)).strftime('%Y%m%d')
    hi = (w + timedelta(days=window_days + 30)).strftime('%Y%m%d')
    found = []
    for i in range(75, len(rows)):
        if not (lo <= rows[i]['date'] <= hi):
            continue
        closes = [r['close'] for r in rows[:i + 1]]
        vols = [r['vol'] for r in rows[:i + 1]]
        c, yc = closes[-1], closes[-2]
        ma20, ma60 = ma(closes, 20), ma(closes, 60)
        if ma20 is None or ma60 is None:
            continue
        if not (c > ma20 > ma60 and yc <= ma20 * 1.02 and c > yc and c >= ma20):
            continue
        if any(closes[j] <= ma20 * 0.97 for j in range(i - 4, i)):
            continue
        avg5 = sum(vols[i - 5:i]) / 5.0
        if avg5 > 0 and vols[i] > avg5 * 1.3:
            continue
        # 必须有“近期回踩”：前20日高点(不含最近4日)比现价高≥3%，排除纯单边不回调
        prior_hi = max(closes[max(0, i - 20):i - 4])
        if prior_hi < c * 1.03:
            continue
        found.append(rows[i]['date'])
    first_all = found[0] if found else None
    inw = [d for d in found if lo <= d <= hi]
    trig = inw[0] if inw else None
    if trig is None:
        return None, None, first_all
    wpos, tpos = idx.get(wd), idx.get(trig)
    if wpos is None or tpos is None:
        return trig, None, first_all
    return trig, tpos - wpos, first_all

WANT = {'E05': ['liquid'], 'E06': ['buy_ai_hard'], 'E08': ['semi_core'],
        'E12': ['buy_semi'], 'E13': ['guosuan_hw']}

def main():
    evs = {}
    try:
        evs = json.load(open(os.path.join(DATA, 'wolf_tech_entries_stockmap.json'), encoding='utf-8'))
    except Exception:
        evs = {}
    rows = []
    for eid, sides in WANT.items():
        cfg = evs.get(eid) or {}
        wd = str(cfg.get('date', '')).replace('-', '')
        for side in sides:
            for sym in (cfg.get('baskets', {}).get(side) or []):
                trig, offset, first_all = find_trend_add(sym, wd)
                wc = price_at(sym, wd)
                w5 = fwd_close(sym, wd, 5)
                s5 = fwd_close(sym, trig, 5) if trig else None
                rows.append({'event': eid, 'wolf_date': wd, 'symbol': sym,
                             'trigger_date': trig, 'first_trigger_all': first_all,
                             'offset_days': offset,
                             'aligned_same_day': bool(trig == wd),
                             'within3': bool(offset is not None and abs(offset) <= 3),
                             'within5': bool(offset is not None and abs(offset) <= 5),
                             'wolf_close': wc, 'wolf_T5': w5, 'sys_T5': s5})
                print(eid, sym, 'wolf', wd, 'trig', trig or '-', 'off', offset, flush=True)
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, 'trend_add_backtest.json')
    json.dump(rows, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', path, 'rows', len(rows), flush=True)
    trig_n = sum(1 for r in rows if r['trigger_date'])
    print('SUMMARY triggered=%d same_day=%d within3=%d within5=%d' % (
        trig_n, sum(r['aligned_same_day'] for r in rows),
        sum(r['within3'] for r in rows), sum(r['within5'] for r in rows)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""backtest_wolf_253_stepwise.py — 253 全护栏 + 254后3日分步回补 5min 回测 v0

目标：用生产口径验证“253 买腿完整护栏(黄线/时间/跌停)”与“254后3日内最多2次小额回补”
对 E05/E06/E08/E12/E13 24 只代理股与 Wolf 事件日的对齐/收益影响。

护栏(代码近似，非30s轮询)：
  - 253：上证任意单根5min close 跌幅≤-0.4%；个股侧需 当日该时点 bar.close>当日累计VWAP(黄线护栏)、
    非跌停(相对前收>-9.5%)、时间在 09:45-14:40、当日未重复触发。
  - 254：沿用生产口径(前日5min低×1.005 + 同序量比≤0.7 + bar.close>cumVWAP)。
  - 分步链：某事件-标的发生 254 后，其后 3 个交易日内若再次出现“放宽量比≤1.2”的 254 型低点，
    允许最多 2 次小额回补(每次1/3口径, 不模拟股数上限)。
局限：代理股≠实盘持仓；假设均有底仓；未模拟30s轮询/冷却时长/T仓上限；5min 量比用同bar序近似。
"""
import os, sys, json
from datetime import datetime

DATA = os.environ.get('DATA_DIR', 'data')
VARIANT = os.environ.get('253_VARIANT', 'A')  # A=站回cumVWAP才买; B=指数急杀+非跌停即买(无VWAP护栏)

def _load(p):
    try:
        return json.load(open(os.path.join(DATA, p), encoding='utf-8'))
    except Exception:
        return {}

def bars_sorted(bars):
    return sorted(bars, key=lambda b: str(b.get('time') or b.get('trade_time')))

def load_stock(code6):
    return _load('stock_5m_bt/%s.json' % code6)

def load_idx():
    raw = _load('index_5min_dh.json')
    return {str(k).replace('-', ''): v for k, v in raw.items()}

def key(s): return str(s).replace('-', '')

def day_low(bars): return min(float(b['low']) for b in bars)
def day_close(bars):
    bs = bars_sorted(bars)
    return float(bs[-1]['close']) if bs else 0.0

def cum_vols(bars):
    out = []; s = 0.0
    for b in bars_sorted(bars):
        s += float(b.get('vol') or 0); out.append(s)
    return out

def cum_amounts(bars):
    out = []; s = 0.0
    for b in bars_sorted(bars):
        s += float(b.get('amount') or 0); out.append(s)
    return out

def find_253_bar(idx_bars):
    """上证当日第一个单根5min close 跌幅<=-0.4% 的 bar time（无则 None）"""
    bs = bars_sorted(idx_bars)
    prev = None
    for b in bs:
        c = float(b['close'])
        if prev and prev > 0 and (c - prev) / prev * 100 <= -0.4:
            return str(b.get('time') or b.get('trade_time'))
        prev = c
    return None

def hm_of(t):
    try:
        return int(str(t)[11:13]) * 100 + int(str(t)[14:16]) if len(str(t)) >= 16 else int(str(t)[8:10]) * 100 + int(str(t)[10:12])
    except Exception:
        return 0

def time_ok(t):
    hm = hm_of(t)
    return 945 <= hm <= 1440

def near_limit_down(bar_close, prev_day_close):
    if not prev_day_close:
        return False
    return (bar_close / prev_day_close - 1) * 100 <= -9.5

def stock_bar_at_or_after(bars, t):
    bs = bars_sorted(bars)
    want = str(t)
    for b in bs:
        bt = str(b.get('time') or b.get('trade_time'))
        if bt >= want:
            return b
    return None

def find_254_on(dm, days, di, thresh_vr=0.7):
    if di <= 0:
        return None
    prev_d = days[di - 1]
    pl = day_low(dm[prev_d])
    if pl <= 0:
        return None
    cur = dm[days[di]]
    cbs = bars_sorted(cur)
    if len(cbs) < 2:
        return None
    base_days = [days[j] for j in range(max(0, di - 5), di)]
    base_cums = []
    for dd in base_days:
        base_cums.append(cum_vols(dm[dd]))
    cums = cum_vols(cur); amts = cum_amounts(cur)
    rlow = 10 ** 18
    for i, b in enumerate(cbs):
        lo = float(b['low']); cl = float(b['close'])
        rlow = min(rlow, lo)
        if rlow <= pl * 1.005 and i >= 1:
            vals = [bc[i] if i < len(bc) else (bc[-1] if bc else 0) for bc in base_cums]
            avg = sum(vals) / len(vals) if vals else 0
            cum = cums[i]
            vr = (cum / avg) if avg > 0 else 0
            vwap = (amts[i] / cum) if cum > 0 else 0
            if vr <= thresh_vr and vwap > 0 and cl > vwap:
                return {'time': str(b.get('time') or b.get('trade_time')), 'close': cl, 'vr': round(vr, 2)}
    return None

def t_plus5(days, dm, di):
    if di + 5 >= len(days):
        return None
    b = day_close(dm[days[di]]); e = day_close(dm[days[di + 5]])
    return round((e / b - 1) * 100, 2) if b else None

WANT = {'E05': ['liquid'], 'E06': ['buy_ai_hard'], 'E08': ['semi_core'],
        'E09': ['packaging_low'], 'E10': ['buy_guosuan'], 'E11': ['materials'],
        'E12': ['buy_semi'], 'E13': ['guosuan_hw']}

def main():
    evs = _load('wolf_tech_entries_stockmap.json')
    idx = load_idx()
    idx_days = sorted(idx.keys())
    rows = []
    for eid, sides in WANT.items():
        cfg = evs.get(eid) or {}
        evdk = key(cfg.get('date', ''))
        for side in sides:
            for sym in (cfg.get('baskets', {}).get(side) or []):
                dm = load_stock(sym[:6])
                days = sorted(dm.keys())
                if evdk not in days:
                    prior = [d for d in days if d <= evdk]
                    if not prior:
                        rows.append({'event': eid, 'symbol': sym, 'error': 'no_5m_data'})
                        continue
                    act_day = prior[-1]   # 事件日在周末/假日 → 用前一个交易日（E09 2026-02-28→02-27）
                else:
                    act_day = evdk
                ei = days.index(act_day)
                lo = max(0, ei - 10); hi = min(len(days), ei + 11)
                actions = []
                # ---- 253 全护栏（逐日独立评估：每个护栏通过的253日都算可执行买点） ----
                actions = []
                for i in range(lo, hi):
                    d = days[i]
                    idxb = idx.get(d)
                    if not idxb:
                        continue
                    t253 = find_253_bar(idxb)
                    if not t253 or not time_ok(t253):
                        continue
                    prev_close = day_close(dm[days[i - 1]]) if i > 0 else 0.0
                    bar = stock_bar_at_or_after(dm[d], t253)
                    if not bar:
                        continue
                    cl = float(bar['close'])
                    cbs = bars_sorted(dm[d]); amts = cum_amounts(dm[d]); vols = cum_vols(dm[d])
                    j = cbs.index(bar) if bar in cbs else -1
                    if j < 0:
                        continue
                    vwap = (amts[j] / vols[j]) if vols[j] > 0 else 0
                    if VARIANT == 'A' and (vwap <= 0 or cl <= vwap):
                        continue  # A版: 黄线护栏(站回cumVWAP才买)
                    # B版: 无VWAP护栏，仅保留非跌停/时间/当日单次
                    if near_limit_down(cl, prev_close):
                        continue
                    if any(a['date'] == d and a['kind'] == '253' for a in actions):
                        continue
                    actions.append({'kind': '253', 'date': d, 'time': t253, 'close': cl})
                # ---- 254（窗口内首次）+ 其后3日内最多2次小额回补 ----
                r254_done = False; step_armed = -1; refills = 0
                for i in range(lo, hi):
                    d = days[i]
                    if not r254_done:
                        r254 = find_254_on(dm, days, i)
                        if r254:
                            actions.append({'kind': '254', 'date': d, 'time': r254['time'], 'close': r254['close']})
                            r254_done = True; step_armed = i; refills = 0
                        continue
                    if step_armed >= 0 and 0 < i - step_armed <= 3 and refills < 2:
                        rr = find_254_on(dm, days, i, thresh_vr=1.2)
                        if rr and not any(a['date'] == d for a in actions):
                            actions.append({'kind': 'step_refill', 'date': d, 'time': rr['time'], 'close': rr['close']})
                            refills += 1
                wolf5 = t_plus5(days, dm, ei)
                sys_t5s = []
                for a in actions:
                    ai = days.index(a['date'])
                    t5 = t_plus5(days, dm, ai)
                    a['T5'] = t5
                    if t5 is not None:
                        sys_t5s.append(t5)
                same = any(a['date'] == act_day for a in actions)
                offs = [days.index(a['date']) - ei for a in actions]
                within3 = any(abs(o) <= 3 for o in offs)
                within5 = any(abs(o) <= 5 for o in offs)
                sys5 = round(sum(sys_t5s) / len(sys_t5s), 2) if sys_t5s else None
                rows.append({'event': eid, 'wolf_date': cfg.get('date'), 'wolf_trade_date': act_day, 'symbol': sym,
                             'wolf_T5': wolf5, 'sys_T5_mean': sys5,
                             'same_day_action': bool(same), 'within3': within3, 'within5': within5,
                             'actions': actions})
                print(eid, sym, 'wolf', cfg.get('date'), 'same', bool(same), 'w3', within3, 'w5', within5,
                      'acts', [(a['kind'], a['date']) for a in actions], flush=True)
    path = os.path.join(DATA, 'stepwise_253_backtest.json' if VARIANT == 'A' else 'stepwise_253_backtest_B.json')
    json.dump(rows, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', path, 'rows', len(rows), flush=True)
    from collections import defaultdict
    agg = defaultdict(lambda: {'n': 0, 'same': 0, 'w3': 0, 'w5': 0})
    for r in rows:
        if r.get('error'):
            continue
        e = agg[r['event']]
        e['n'] += 1
        e['same'] += int(r['same_day_action'])
        e['w3'] += int(r['within3'])
        e['w5'] += int(r['within5'])
    for e, v in sorted(agg.items()):
        print('AGG', e, 'n', v['n'], 'same', v['same'], 'within3', v['w3'], 'within5', v['w5'], flush=True)
    print('DONE', flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

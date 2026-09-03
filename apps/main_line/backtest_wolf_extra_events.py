# -*- coding: utf-8 -*-
"""backtest_wolf_extra_events.py — E01-E04/E15 扩展回放 v0（stepwise B 口径扩展）

目标：把此前因“无5min文件”而不计一致率的 E01-E04/E15 补进动作回放候选：
  - E03(2025-10-28 指数回补)/E04(2025-10-30 ETF 254)/E15(2026-08-12 半导体refill)
    → 复用 stepwise_253_backtest_B 的 253+254+step_refill 逻辑（VARIANT=B, 无VWAP护栏）
    → 253 大盘急杀时点: 优先上证 index_5min_dh（E03/E04 窗口 2025-10-09~2025-11-12 已用 datahubco 真上证 5min 补齐）；
      个别缺失日仍回退 510300.SH 宽基ETF代理兜底（偏差记入 caveats）
  - E01(2025-08-21 开盘买存储+华勤)/E02(2025-09-10 开盘CPO probe≤3%)
    → intent_open 语义: 事件前一交易日当日开盘动作(时间=当日首根5min bar)
      = “主线候选+P3 new_base/probe 通道放行后开盘执行”的系统等价假设
输出: data/stepwise_253_backtest_B_extra.json（与 B 版同 schema, 事件=E01-E04/E15）
"""
import os, sys, json
from collections import defaultdict

os.environ.setdefault('253_VARIANT', 'B')
DATA = os.environ.get('DATA_DIR', 'data')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_wolf_253_stepwise as S  # 复用 find_253_bar/find_254_on/t_plus5 等

INTENT_OPEN = {'E01', 'E02'}   # 开盘建仓/probe 语义
STEPWISE_EXTRA = {'E03': ['semi_cpo_refill'], 'E04': ['etf'], 'E15': ['semi_core']}


def load(p):
    try:
        return json.load(open(os.path.join(DATA, p), encoding='utf-8'))
    except Exception:
        return {}


def idx_getter():
    """real(上证5min) 优先; 缺失日回退 510300 宽基代理(键统一YYYYMMDD)。"""
    real = {str(k).replace('-', ''): v for k, v in (load('index_5min_dh.json') or {}).items()}
    proxy = load('stock_5m_bt/510300.json') or {}
    return lambda d: real.get(d) or proxy.get(d)


def run_stepwise(eid, sides, cfg):
    idxget = idx_getter()
    evdk = S.key(cfg.get('date', ''))
    out_rows = []
    for side in sides:
        for sym in (cfg.get('baskets', {}).get(side) or []):
            dm = S.load_stock(sym[:6])
            days = sorted(dm.keys())
            if evdk not in days:
                prior = [d for d in days if d <= evdk]
                if not prior:
                    out_rows.append({'event': eid, 'symbol': sym, 'error': 'no_5m_data'})
                    continue
                act_day = prior[-1]
            else:
                act_day = evdk
            ei = days.index(act_day)
            lo = max(0, ei - 10); hi = min(len(days), ei + 11)
            actions = []
            for i in range(lo, hi):          # 253 (大盘急杀日, 需宽基/上证有当日bar)
                d = days[i]
                idxb = idxget(d)
                if not idxb:
                    continue
                t253 = S.find_253_bar(idxb)
                if not t253 or not S.time_ok(t253):
                    continue
                prev_close = S.day_close(dm[days[i - 1]]) if i > 0 else 0.0
                bar = S.stock_bar_at_or_after(dm[d], t253)
                if not bar:
                    continue
                if S.near_limit_down(float(bar['close']), prev_close):
                    continue
                if any(a['date'] == d and a['kind'] == '253' for a in actions):
                    continue
                actions.append({'kind': '253', 'date': d, 'time': t253, 'close': float(bar['close'])})
            r254_done = False; step_armed = -1; refills = 0     # 254 + 后3日≤2次小额回补
            for i in range(lo, hi):
                d = days[i]
                if not r254_done:
                    r254 = S.find_254_on(dm, days, i)
                    if r254:
                        actions.append({'kind': '254', 'date': d, 'time': r254['time'], 'close': r254['close']})
                        r254_done = True; step_armed = i; refills = 0
                    continue
                if step_armed >= 0 and 0 < i - step_armed <= 3 and refills < 2:
                    rr = S.find_254_on(dm, days, i, thresh_vr=1.2)
                    if rr and not any(a['date'] == d for a in actions):
                        actions.append({'kind': 'step_refill', 'date': d, 'time': rr['time'], 'close': rr['close']})
                        refills += 1
            wolf5 = S.t_plus5(days, dm, ei)
            sys_t5s = []
            for a in actions:
                ai = days.index(a['date'])
                t5 = S.t_plus5(days, dm, ai)
                a['T5'] = t5
                if t5 is not None:
                    sys_t5s.append(t5)
            offs = [days.index(a['date']) - ei for a in actions]
            out_rows.append({'event': eid, 'wolf_date': cfg.get('date'), 'wolf_trade_date': act_day,
                             'symbol': sym, 'wolf_T5': wolf5,
                             'sys_T5_mean': round(sum(sys_t5s) / len(sys_t5s), 2) if sys_t5s else None,
                             'same_day_action': bool(any(a['date'] == act_day for a in actions)),
                             'within3': any(abs(o) <= 3 for o in offs),
                             'within5': any(abs(o) <= 5 for o in offs),
                             'actions': actions})
            print(eid, sym, 'acts', [(a['kind'], a['date']) for a in actions], flush=True)
    return out_rows


def run_intent_open(eid, cfg):
    """开盘意图动作：事件前一交易日开盘(首根5min)即视为系统等价执行(new_base/probe通道)。"""
    evdk = S.key(cfg.get('date', ''))
    rows = []
    for side, syms in (cfg.get('baskets') or {}).items():
        if isinstance(syms, str):
            continue
        for sym in syms:
            dm = S.load_stock(sym[:6])
            days = sorted(dm.keys())
            if evdk not in days:
                prior = [d for d in days if d <= evdk]
                if not prior:
                    rows.append({'event': eid, 'symbol': sym, 'error': 'no_5m_data'})
                    continue
                act_day = prior[-1]
            else:
                act_day = evdk
            ei = days.index(act_day)
            bs = S.bars_sorted(dm[act_day])
            if not bs:
                rows.append({'event': eid, 'symbol': sym, 'error': 'no_bar'})
                continue
            first = bs[0]
            actions = [{'kind': 'intent_open', 'date': act_day, 'time': str(first['time']),
                        'close': float(first['close']), 'T5': S.t_plus5(days, dm, ei)}]
            rows.append({'event': eid, 'wolf_date': cfg.get('date'), 'wolf_trade_date': act_day,
                         'symbol': sym, 'wolf_T5': S.t_plus5(days, dm, ei),
                         'sys_T5_mean': actions[0]['T5'], 'same_day_action': True,
                         'within3': True, 'within5': True, 'actions': actions})
            print(eid, sym, 'intent_open', act_day, flush=True)
    return rows


def main():
    sm = load('wolf_tech_entries_stockmap.json') or {}
    rows = []
    for eid in ['E01', 'E02', 'E03', 'E04', 'E15']:
        cfg = sm.get(eid) or {}
        if not cfg:
            print('NO CFG', eid); continue
        if eid in INTENT_OPEN:
            rows += run_intent_open(eid, cfg)
        else:
            rows += run_stepwise(eid, STEPWISE_EXTRA.get(eid, []), cfg)
    path = os.path.join(DATA, 'stepwise_253_backtest_B_extra.json')
    json.dump(rows, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    agg = defaultdict(lambda: {'n': 0, 'same': 0, 'w5': 0})
    for r in rows:
        if r.get('error'):
            continue
        a = agg[r['event']]
        a['n'] += 1; a['same'] += int(r['same_day_action']); a['w5'] += int(r['within5'])
    for e, v in sorted(agg.items()):
        print('AGG', e, 'n', v['n'], 'same', v['same'], 'w5', v['w5'],
              'cov', round(v['w5'] / v['n'], 3), flush=True)
    print('WROTE', path, 'rows', len(rows), flush=True)
    print('DONE', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

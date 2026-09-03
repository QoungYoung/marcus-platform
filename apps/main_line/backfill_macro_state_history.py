# -*- coding: utf-8 -*-
"""backfill_macro_state_history.py — 历史 macro_state 关键时点回填（不做逐日全量）

范围（关键时点）：
  - Wolf 事件行 E05..E15（data/wolf_reason_events.json 权威，缺文件回退内置表）
  - Wolf 宏观/机构表态 A/B M01..M10（backtest_macro_wolf.LABELS 权威）
按日期取“当时可见”快照：akshare bond_zh_us_rate 按 as-of 取 <= 日期最近一期收益率；
tushare margin/fund_share(510300/510050)/moneyflow_hsgt/top_list 均以 end_date=日期 回看，
开关用 build_macro_state._derive_switches 同源推导。DXY 只有新浪实时、无历史源 → 置空不推导。

输出: data/macro_state_history.json
  { meta:{scope, generated_at, date_count, labels:{date:[id...]}, note_dxy},
    dates:[...], states:{date: <与 data/macro_state.json 同构的当日快照>} }

用法:
  python -u apps/main_line/backfill_macro_state_history.py
  python -u apps/main_line/backfill_macro_state_history.py --dates 2026-07-08,2026-07-23
  python -u apps/main_line/backfill_macro_state_history.py --out data/macro_state_history.json --sleep 0.3
"""
import os, sys, json, time, datetime as _dt

DATA = os.environ.get('DATA_DIR', 'data')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_macro_state as bm   # noqa: E402
import wave_agent as wa          # noqa: E402

DXY_NOTE = '历史回填无实时DXY源(Sina仅实时, FRED/Yahoo不可达); 开关推导不使用DXY'

# Wolf 可测/可解释事件 E05..E15（E01-E04 无 2025-11-24 前分钟窗口，宏观回填意义低，不纳入）
EVENT_IDS = ['E05', 'E06', 'E07', 'E08', 'E09', 'E10', 'E11', 'E12', 'E13', 'E14', 'E15']
_EVENT_FALLBACK = {
    'E05': '2025-12-09', 'E06': '2025-12-31', 'E07': '2026-01-05',
    'E08': '2026-01-14', 'E09': '2026-02-27', 'E10': '2026-03-19',
    'E11': '2026-04-22', 'E12': '2026-06-05', 'E13': '2026-07-08',
    'E14': '2026-07-23', 'E15': '2026-08-12',
}


def load_event_dates():
    """data/wolf_reason_events.json 为权威事件表；缺文件/坏文件回退内置表。"""
    try:
        p = os.path.join(DATA, 'wolf_reason_events.json')
        if os.path.exists(p):
            data = json.load(open(p, encoding='utf-8'))
            evs = data if isinstance(data, list) else data.get('events', [])
            out = {ev['id']: ev['date'] for ev in evs
                   if isinstance(ev, dict) and ev.get('id') in EVENT_IDS and ev.get('date')}
            if out:
                return out
    except Exception as e:
        print('event load fallback', str(e)[:80], flush=True)
    return dict(_EVENT_FALLBACK)


def macro_label_dates():
    """M01..M10 日期以 backtest_macro_wolf.LABELS 为权威（该模块已做 __main__ 守卫，导入无副作用）。"""
    from backtest_macro_wolf import LABELS
    return {L['id']: L['date'] for L in LABELS}


def build_scope():
    """date -> [所属事件/表态 id 列表]"""
    by = {}
    for k, v in macro_label_dates().items():
        by.setdefault(v, []).append(k)
    for k, v in load_event_dates().items():
        by.setdefault(v, []).append(k)
    return {d: sorted(set(ids)) for d, ids in by.items()}


def _cache_bond_rates_once():
    """akshare bond_zh_us_rate 全表只拉一次，各 as-of 日期共享（历史数据不变）。"""
    cache = {}
    orig = bm.ak.bond_zh_us_rate
    def _cached():
        if 'df' not in cache:
            cache['df'] = orig()   # 先留原函数引用再打补丁，避免自递归
        return cache['df']
    try:
        bm.ak.bond_zh_us_rate = _cached
    except Exception:
        pass  # 补丁失败则每个日期各自拉一次，不影响正确性


def snapshot_for(target):
    """复刻 build_macro_state.main() 的单日结构（dxy 历史无源置空），供回填文件使用。"""
    out = {
        'date': target,
        'yields': {}, 'dxy': {'value': None, 'time': None, 'note': DXY_NOTE},
        'market': {},
        'meta': {'as_of': target, 'source': 'backfill-key-dates',
                 'generated_at': _dt.datetime.now().isoformat()},
    }
    try:
        cur, prev = bm.rate_snapshot(target)
        if cur:
            out['yields']['cn'] = {k.replace('中国国债收益率', '').replace('-', '_'): cur[k]
                                   for k in ['中国国债收益率2年', '中国国债收益率5年', '中国国债收益率10年',
                                             '中国国债收益率30年', '中国国债收益率10年-2年']}
            out['yields']['us'] = {k.replace('美国国债收益率', '').replace('-', '_'): cur[k]
                                   for k in ['美国国债收益率2年', '美国国债收益率5年', '美国国债收益率10年',
                                             '美国国债收益率30年', '美国国债收益率10年-2年']}
            if prev:
                out['yields']['chg'] = {
                    'cn30_5d_dummy': None,
                    'cn30_1d': (cur['中国国债收益率30年'] - prev['中国国债收益率30年'])
                    if cur['中国国债收益率30年'] is not None and prev['中国国债收益率30年'] is not None else None,
                    'us10_1d': (cur['美国国债收益率10年'] - prev['美国国债收益率10年'])
                    if cur['美国国债收益率10年'] is not None and prev['美国国债收益率10年'] is not None else None,
                    'us30_1d': (cur['美国国债收益率30年'] - prev['美国国债收益率30年'])
                    if cur['美国国债收益率30年'] is not None and prev['美国国债收益率30年'] is not None else None,
                }
            print('yields ok cn30', out['yields']['cn'].get('30年'), 'us10', out['yields']['us'].get('10年'), flush=True)
        else:
            print('yields no data', flush=True)
    except Exception as e:
        print('yields ERR', str(e)[:200], flush=True)
        out['yields']['error'] = str(e)[:200]
    try:
        ctx = wa.get_market_context(target)
        out['market'] = {k: ctx.get(k) for k in
                         ['idx_close', 'idx_r5', 'idx_r20', 'margin_rzrqye', 'margin_20d_chg',
                          'margin_net_buy', 'margin_rzrqye_pct', 'north_5d', 'north_today', 'gjd',
                          'turnover_rate', 'pe_ttm', 'vol_ratio_5_60', 'vol_pct120']}
        out['market']['lhb'] = bm._lhb_snapshot(wa._ts_pro(), target)
        gjd = out['market'].get('gjd') or {}
        print('market ok margin20d', out['market'].get('margin_20d_chg'),
              'gjd300_chg20', gjd.get('sh300_chg20'), flush=True)
    except Exception as e:
        print('market ERR', str(e)[:200], flush=True)
        out['market']['error'] = str(e)[:200]
    try:
        bm._derive_switches(out)
    except Exception as e:
        out['macro_switches'] = {'flags': [], 'err': str(e)[:120]}
    return out


def _parse_args(argv):
    dates, out, sleep, force = None, os.path.join(DATA, 'macro_state_history.json'), 0.3, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--dates' and i + 1 < len(argv):
            dates = [x.strip() for x in argv[i + 1].split(',') if x.strip()]
            i += 2
        elif a == '--out' and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
        elif a == '--sleep' and i + 1 < len(argv):
            sleep = float(argv[i + 1])
            i += 2
        elif a == '--force':
            force = True
            i += 1
        else:
            i += 1
    return dates, out, sleep, force


def _dump_payload(out_path, meta, states):
    payload = {
        'meta': meta,
        'dates': sorted(states.keys()),
        'states': states,
    }
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    json.dump(payload, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def main():
    argv = sys.argv[1:]
    dates_arg, out_path, sleep_s, force = _parse_args(argv)
    scope = build_scope()
    if dates_arg:
        dates = sorted(set(dates_arg))
    else:
        dates = sorted(scope.keys())
    if not dates:
        print('NO DATES'); return
    _cache_bond_rates_once()
    existing = {}
    if not force and os.path.exists(out_path):
        try:
            existing = json.load(open(out_path, encoding='utf-8')).get('states') or {}
        except Exception:
            existing = {}
    todo = [d for d in dates if d not in existing]
    print('scope labels', json.dumps({d: scope[d] for d in dates}, ensure_ascii=False), flush=True)
    print('total', len(dates), 'todo', len(todo), 'already_done', len(existing), flush=True)
    states = dict(existing)
    for i, d in enumerate(todo):
        print('==== backfill', d, '/', scope.get(d, []), flush=True)
        st = snapshot_for(d)
        states[d] = st
        flags = sorted((st.get('macro_switches') or {}).get('flags') or [])
        m = st.get('market') or {}
        y = st.get('yields') or {}
        gjd = m.get('gjd') or {}
        row = (d, '/'.join(scope.get(d, [])), ','.join(flags) or '-',
               m.get('margin_20d_chg'), m.get('margin_net_buy'),
               gjd.get('sh300_chg20'), gjd.get('sh50_chg20'),
               (y.get('cn') or {}).get('30年'), (y.get('us') or {}).get('10年'))
        meta = {
            'scope': 'Wolf关键时点: 事件E05-E15 ∪ 宏观表态M01-M10（仅关键时点，非逐日全量）',
            'generated_at': _dt.datetime.now().isoformat(),
            'last_updated': _dt.datetime.now().isoformat(),
            'labels': {x: scope.get(x, []) for x in sorted(states.keys())},
            'note_dxy': DXY_NOTE,
            'note_rates': 'akshare bond_zh_us_rate 按 as-of <= 日期取最近一期, 同 build_macro_state',
            'note_market': 'tushare margin/fund_share/moneyflow_hsgt/top_list end_date=日期 回看',
        }
        _dump_payload(out_path, meta, states)
        print('==', d, 'ok', ' | '.join('' if x is None else str(x) for x in row[1:]), flush=True)
        if i < len(todo) - 1 and sleep_s > 0:
            time.sleep(sleep_s)
    print('date | labels | flags | margin20d% | margin_net | sh300_chg20% | sh50_chg20% | cn30y | us10y', flush=True)
    for d in sorted(states.keys()):
        st = states[d]
        flags = sorted((st.get('macro_switches') or {}).get('flags') or [])
        m = st.get('market') or {}
        y = st.get('yields') or {}
        gjd = m.get('gjd') or {}
        row = (d, '/'.join(scope.get(d, [])), ','.join(flags) or '-',
               m.get('margin_20d_chg'), m.get('margin_net_buy'),
               gjd.get('sh300_chg20'), gjd.get('sh50_chg20'),
               (y.get('cn') or {}).get('30年'), (y.get('us') or {}).get('10年'))
        print(' | '.join('' if x is None else str(x) for x in row), flush=True)
    print('WROTE', out_path, 'dates', len(states), flush=True)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()

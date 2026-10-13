# -*- coding: utf-8 -*-
"""build_concept_long.py — concept_long 长历史概念收盘指数(2026-09-08)
源: Tushare 中继(2026-09-13 起 datahubco 基础接口 + promax 聚合接口, 替代已失效的 gzcloud 代理)
     全市场 daily(trade_date 逐日批量, 实测 5300+行/日; 逐日全市场为调用数最少的批量形态)。
方法: THEME_CONCEPTS 104概念 x stock_pool.db stock_concept_map 全量成分(4676只去重)
      -> 等权 pct 环比累计合成概念收盘指数(规避未复权除权跳变; 停牌沿用昨收涨跌0)。
产物: /app/data/concept_long.json {meta, dates:[交易日], series:{concept:[100,...]}}  起点20250101
用法: python3 build_concept_long.py [YYYYMMDD起始,默认20250101]
"""
import sys, os, json, sqlite3, time, requests, urllib3


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
urllib3.disable_warnings()
DATA = os.environ.get('DATA_DIR', '/app/data')


def _relay():
    """加载 core/tushare_relay.py（datahubco + promax，替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib
    try:
        return importlib.import_module('tushare_relay')
    except ImportError as _e_sil1:
        _silent_alert("build_concept_long.py:23", _e_sil1)
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / 'core' / 'tushare_relay.py').exists():
            sys.path.insert(0, str(p / 'core'))
            return importlib.import_module('tushare_relay')
    raise ImportError('core/tushare_relay.py 未找到')


def gz(params, fields='ts_code,close'):
    try:
        _fields, items = _relay().relay_items('daily', fields=fields, **params)
        return items or []
    except Exception as e:
        print('TUSHARE_FAIL daily', str(e)[:120], file=sys.stderr)
        return []


def cal(s, e):
    _fields, items = _relay().relay_items('trade_cal', exchange='SSE', start_date=s, end_date=e,
                                          fields='cal_date,is_open')
    return sorted(x[0] for x in items if x[1] == 1)


def main():
    argv = sys.argv[1:]
    seed_mode = '--seed' in argv
    start = argv[0] if argv and not argv[0].startswith('-') else '20250101'
    end = time.strftime('%Y%m%d')
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    members = {}   # concept -> [ts]
    ts_conc = {}   # ts -> [concept]
    if seed_mode:
        sys.path.insert(0, '/app/apps/main_line')
        import chain_map as cm
        src = {}
        for t, segs in cm.SEED.items():
            for s in segs:
                for c in s.get('concepts', []):
                    src.setdefault(c, True)
    else:
        from fusion_mainline import THEME_CONCEPTS
        src = {c: True for cons in THEME_CONCEPTS.values() for c in cons}
    for c in src:
        rows = [r[0] for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,))]
        rows = list(dict.fromkeys(rows))
        members[c] = rows
        for ts in rows: ts_conc.setdefault(ts, []).append(c)
    all_ts = list(ts_conc)
    print('concepts', len(members), 'members', len(all_ts), flush=True)
    # ── ★ 2026-10-13（用户选 A ✓）：**真增量**（additive ✓：旧的"全量"路径原样保留 ✓）──────
    #   病灶 ✗：只有"跳过 or 从 20250101 全量"（一次 ~40 分钟 ✗）
    #   做法 ✓：读旧 concept_long.json ⇒ 只算 (旧 end, 今天] ⇒ **追加**到旧序列尾 ✓
    #   开关 `CONCEPT_LONG_INCREMENTAL`（默认 1 ✓，置 0 回老行为 ✓）；
    #   任何不一致（旧文件坏/概念缺/旧收盘取不到）⇒ 打印原因并**回退全量** ✓
    _fp = os.path.join(DATA, 'concept_long_seed.json' if seed_mode else 'concept_long.json')
    if (not seed_mode
            and str(os.getenv('CONCEPT_LONG_INCREMENTAL', '1')).strip().lower() not in ('0', 'false', 'no')
            and os.path.exists(_fp)):
        try:
            _old = json.load(open(_fp, encoding='utf-8'))
            _oend = str(((_old.get('meta') or {}).get('end')) or '')
            _oser = _old.get('series') or {}
            _miss = [c for c in members if c not in _oser]
            if _oend and _oend < end and not _miss:
                _cal0 = cal('20250101', _oend)
                _seed_d = _cal0[-1] if _cal0 else _oend
                _rows0 = gz({'trade_date': _seed_d})
                _last0 = {}
                for _x in _rows0:
                    try:
                        _last0[_x[0]] = float(_x[1])
                    except Exception:
                        pass
                _new = [d for d in cal(_oend, end) if _oend < d <= end]
                if _last0 and _new:
                    _dates = {}
                    _cum = {}
                    for c in members:
                        _dates[c] = list(_oser[c].get('dates') or [])
                        _cum[c] = list(_oser[c].get('close') or [])
                    _first = {c: bool(_dates[c]) for c in members}
                    _last = dict(_last0)
                    print('[inc] 增量 ✓ 旧 end=%s（seed=%s，%d 只收盘）⇒ 追加 %d 天 ✓'
                          % (_oend, _seed_d, len(_last0), len(_new)), flush=True)
                    _t0i = time.time()
                    for _di, _d in enumerate(_new):
                        _px = {}
                        for _x in gz({'trade_date': _d}):
                            try:
                                _px[_x[0]] = float(_x[1])
                            except Exception:
                                pass
                        for _c, _mts in members.items():
                            _tot = 0.0
                            _n = 0
                            for _ts in _mts:
                                _pr = _last.get(_ts)
                                _cn = _px.get(_ts)
                                if _pr is not None:
                                    if _cn is not None:
                                        _tot += (_cn / _pr - 1.0)
                                    _n += 1
                            if _n > 0:
                                _p = _tot / _n
                                if not _first[_c]:
                                    _cum[_c].append(100.0)
                                    _dates[_c].append(_d)
                                    _first[_c] = True
                                _cum[_c].append(_cum[_c][-1] * (1.0 + _p))
                                _dates[_c].append(_d)
                            elif _first[_c]:
                                _cum[_c].append(_cum[_c][-1])
                                _dates[_c].append(_d)
                        for _ts, _cn in _px.items():
                            if _ts in ts_conc:
                                _last[_ts] = _cn
                        if (_di + 1) % 20 == 0 or _di == len(_new) - 1:
                            print('[inc] day %d / %d %s %.0fs' % (_di + 1, len(_new), _d, time.time() - _t0i), flush=True)
                    # 一致性自检 ✓（长度对齐 + 不倒退 ✓）⇒ 不过就回退全量 ✗
                    _bad = [c for c in members if len(_dates[c]) != len(_cum[c])]
                    _series = {c: {'dates': _dates[c], 'close': _cum[c]} for c in members}
                    if _bad:
                        print('[inc] 自检失败(长度不齐 %d 个概念) ⇒ 回退全量 ✗' % len(_bad), flush=True)
                    else:
                        _out = {'meta': {'generator': 'build_concept_long_v1',
                                         'start': start, 'end': end,
                                         'source': 'gzcloud daily 全市场等权pct累计',
                                         'concepts': len(_series), 'members': len(all_ts),
                                         'note': '未复权已用pct环比规避; 停牌沿用昨收',
                                         'incremental': True, 'appended_days': len(_new),
                                         'prev_end': _oend},
                                'series': _series}
                        json.dump(_out, open(_fp, 'w', encoding='utf-8'), ensure_ascii=False)
                        print('WROTE(inc) %s size %d KB, appended %d 天 ✓'
                              % (_fp, os.path.getsize(_fp) // 1024, len(_new)), flush=True)
                        return
                else:
                    print('[inc] 无需追加（new=%d，旧收盘=%d）⇒ 检查是否已最新 ✓' % (len(_new), len(_last0)), flush=True)
            else:
                print('[inc] 不满足增量条件（旧 end=%s，缺概念 %d 个）⇒ 全量 ✓' % (_oend or '-', len(_miss)), flush=True)
        except Exception as _e_inc:
            print('[inc] 增量失败 ⇒ 回退全量 ✗: %s: %s' % (type(_e_inc).__name__, str(_e_inc)[:100]), flush=True)
    opens = [d for d in cal(start, end) if d >= start]
    print('days', len(opens), start, '->', end, flush=True)
    last = {}                     # ts -> prev close
    last_cnt = {}                 # ts 首次出现前不可算
    cum = {c: [] for c in members}
    first_done = {c: False for c in members}
    t0 = time.time()
    for di, d in enumerate(opens):
        rows = gz({'trade_date': d})
        px = {}
        for x in rows:
            try: px[x[0]] = float(x[1])
            except Exception: pass
        for c, mts in members.items():
            tot = 0.0; n = 0
            for ts in mts:
                prev = last.get(ts)
                c_now = px.get(ts)
                if prev is not None:
                    if c_now is not None:
                        tot += (c_now / prev - 1.0)
                    n += 1
            if n > 0:
                pct = tot / n
                if not first_done[c]:
                    cum[c].append(100.0); first_done[c] = True
                cum[c].append(cum[c][-1] * (1.0 + pct))
            elif first_done[c]:
                cum[c].append(cum[c][-1])  # 全员停牌沿用
        for ts, c in px.items():
            if ts in ts_conc: last[ts] = c
        if (di + 1) % 50 == 0 or di == len(opens) - 1:
            print('day', di + 1, '/', len(opens), d, '%.0fs' % (time.time() - t0), flush=True)
    series = {}
    for c in members:
        # 与 opens 对齐(首个有效日起)
        off = len(opens) - len(cum[c])
        dates = opens[off:] if off > 0 else opens
        series[c] = {'dates': dates, 'close': cum[c]}
    out = {'meta': {'generator': 'build_concept_long_v1' + ('_seed' if seed_mode else ''), 'start': start, 'end': end,
                    'source': 'gzcloud daily 全市场等权pct累计', 'concepts': len(series),
                    'members': len(all_ts), 'note': '未复权已用pct环比规避; 停牌沿用昨收'},
           'series': series}
    p = os.path.join(DATA, 'concept_long_seed.json' if seed_mode else 'concept_long.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    print('WROTE', p, 'size', os.path.getsize(p) // 1024, 'KB, %.0fs' % (time.time() - t0), flush=True)

if __name__ == '__main__':
    main()

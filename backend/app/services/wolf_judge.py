# -*- coding: utf-8 -*-
"""wolf_judge.py — 狼大"要不要出手"六因子规则打分(生产版, 权重/阈值照 backtest_wolf_t_consistency_v10)

六因子 = wave(0.15)+关键位深度key(0.20)+量能vol(0.15)+计划/关键位plan(0.25)+位置分位pos(0.15)+主线main(0.10)
阈值: >=0.72 allow(可出手) | 0.65~0.72 boundary(交LLM带计划/关键位/浪型语境复核) | <0.65 block(低分倾向观望)

**召回安全设计**: 本模块只产出 score/gate/prompt(供 trade_graph 注入 Pi), 不做硬拦;
真正的硬门由 wave_level_gate(operation=defense/exit->不建仓) 与 weekend_de_risk 承担。
原因: 09-02(狼大买日) 3候选六因子均<0.65, 硬拦会掉召回(100%->75%)。
"""
import os, json

_DATA = os.environ.get('DATA_DIR', '/app/data')
THR_ALLOW = 0.72
THR_BOUNDARY = 0.65
W_WV, W_KEY, W_VOL, W_PLAN, W_POS, W_MAIN = 0.15, 0.20, 0.15, 0.25, 0.15, 0.10
DEFAULT_SYMS = ['159516', '588170', '301018']


def _load(p):
    try:
        return json.load(open(os.path.join(_DATA, p), encoding='utf-8'))
    except Exception:
        return {}


def _daily(code):
    out = {}
    for root in ('recent_sync', 'stock_5m_bt'):
        p = os.path.join(_DATA, root, '%s.json' % code)
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        for k, v in d.items():
            bs = sorted(v, key=lambda x: str(x.get('time') or x.get('trade_time')))
            if bs:
                out.setdefault(k, bs)
    return out


def _ma20(date):
    rows = sorted(_load('index_daily_000001.json'), key=lambda x: int(str(x['trade_date']).replace('-', '')))
    rows = [r for r in rows if int(str(r['trade_date']).replace('-', '')) <= int(date)]
    if len(rows) < 20:
        return None
    return sum(float(r['close']) for r in rows[-20:]) / 20.0


def _idx_low(date):
    for p in ('index_5min_dh.json',):
        for k, v in _load(p).items():
            if str(k).replace('-', '') == date:
                bs = sorted(v, key=lambda x: str(x.get('time') or x.get('trade_time')))
                if bs:
                    return min(float(b['low']) for b in bs)
    for k, v in (_load('recent_sync/index5_sina.json') or {}).items():
        if str(k).replace('-', '') == date:
            bs = sorted(v, key=lambda x: str(x.get('time') or x.get('trade_time')))
            if bs:
                return min(float(b['low']) for b in bs)
    return None


def _tech_dd(date):
    best = None
    for c in ('159516', '588170'):
        dc = _daily(c)
        bs = dc.get(date)
        if not bs:
            continue
        hi = max(float(b['high']) for b in bs)
        lo = min(float(b['low']) for b in bs)
        dd = (lo / hi - 1) * 100 if hi > 0 else 0
        best = dd if best is None else min(best, dd)
    return best


def _pos_pct(dc, date):
    ks = sorted(k for k in dc if k <= date and dc[k])
    if len(ks) < 20:
        return 50
    vals = [float(dc[k][-1]['close']) for k in ks[-60:]]
    cur = float(dc[ks[-1]][-1]['close'])
    lo, hi = min(vals), max(vals)
    return (cur - lo) / (hi - lo) * 100 if hi > lo else 50


def _wave_raw(date):
    try:
        ws = _load('wave_state.json') or {}
        if str(ws.get('date') or ws.get('as_of') or date).replace('-', '') == date:
            lv = ws.get('level')
            if lv:
                return lv
    except Exception:
        pass
    return 't_only'


def six_factor_score(sym, date=None):
    """返回 (score, detail). sym 为候选代码(6位)."""
    import datetime as _dt
    date = date or _dt.datetime.now().strftime('%Y%m%d')
    dc = _daily(sym)
    bs = dc.get(date)
    if not bs:
        return None, 'no_bars'
    prev = [dc[k] for k in sorted(dc) if k < date and dc[k]][-5:]
    if not prev:
        return None, 'no_prev'
    wv_raw = _wave_raw(date)
    wv = 1.0 if wv_raw in ('t_only', 'side') else 0.3
    m20 = _ma20(date)
    low = _idx_low(date)
    keyd = max(0.0, (m20 - low) / m20 * 100) if (m20 and low) else 0
    key = min(1.0, keyd / 1.0)
    pv = [sum(float(b.get('vol') or 0) for b in p) for p in prev[:5]]
    avg = sum(pv) / len(pv) if pv else 0
    cur = sum(float(b.get('vol') or 0) for b in bs)
    vol = 1.0 if (avg > 0 and cur <= avg * 0.9) else 0.2
    dd = _tech_dd(date)
    plan = 1.0 if (keyd >= 0.3 or (dd is not None and dd <= -3.0)) else 0.3
    pp = _pos_pct(dc, date)
    pos = 1.0 if pp < 40 else (0.6 if pp < 60 else 0.2)
    main = 1.0
    s = W_WV * wv + W_KEY * key + W_VOL * vol + W_PLAN * plan + W_POS * pos + W_MAIN * main
    detail = {'wave': wv_raw, 'keyd': round(keyd, 2), 'tech_dd': round(dd, 2) if dd is not None else None,
              'vol': round(vol, 2), 'plan': round(plan, 2), 'pos_pct': round(pp, 1), 'main': round(main, 2)}
    return round(s, 3), detail


def buy_gate(score):
    if score is None:
        return 0
    if score >= THR_ALLOW:
        return 2
    if score >= THR_BOUNDARY:
        return 1
    return 0


def gate_label(g):
    return {0: 'block', 1: 'boundary', 2: 'allow'}.get(g, 'unknown')


def _hint(detail):
    if not isinstance(detail, dict):
        return ''
    return 'wave=%s keyd=%.2f vol=%.2f plan=%.2f pos=%.1f%% main=%.2f' % (
        detail.get('wave'), detail.get('keyd', 0), detail.get('vol', 0), detail.get('plan', 0),
        detail.get('pos_pct', 0), detail.get('main', 0))


def day_six_factor(syms=None, date=None):
    """对候选集合算六因子, 返回 (max_score, best_sym, gate, detail). 供 trade_graph 注入 prompt."""
    import datetime as _dt
    syms = syms or DEFAULT_SYMS
    date = date or _dt.datetime.now().strftime('%Y%m%d')
    best = None
    for s in syms:
        sc, det = six_factor_score(s, date)
        if sc is None:
            continue
        g = buy_gate(sc)
        if best is None or sc > best[0]:
            best = (sc, s, g, det)
    if best is None:
        return None, None, 0, 'no_candidates'
    return best[0], best[1], best[2], best[3]


def day_gate_prompt(syms=None, date=None):
    """返回注入 Pi 的软指导块(不硬拦, 保召回)."""
    import datetime as _dt
    date = date or _dt.datetime.now().strftime('%Y%m%d')
    sc, sym, g, det = day_six_factor(syms, date)
    if sc is None:
        return ''
    if g == 2:
        tail = '符合狼大风格买点，可出手。'
    elif g == 1:
        tail = '边界样本，请结合狼大式计划(关键位回补/半年计划/科技急杀)、关键位、浪型语境做最终判断；确认符合狼大买点再出手，否则观望。'
    else:
        tail = '低分，倾向观望/不出手；除非你有基于计划/关键位/浪型的强证据，否则不应建仓。'
    return ('【六因子规则打分(软指导,不硬拦)】今日候选%s 得分 %.2f（%s）——%s'
            % (sym, sc, _hint(det), tail))

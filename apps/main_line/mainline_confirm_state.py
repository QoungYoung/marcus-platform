# -*- coding: utf-8 -*-
"""mainline_confirm_state.py — 主线确认历史与低吸准入(2026-09-09, Wolf 口径落地)
规则依据(狼大语料核验, 详见 docs/wolf-dip-entry-rule.md):
  低吸资格 = 主题「曾确认」(过去 LOOKBACK 交易日内 mainline_gate verdict=confirmed_candidate)
            —— Wolf: 只有『已经确定了主线』的 2 浪回调才低吸(2025-02 原话), 未确认大2浪不接(材料案例 2026-08-04)。
状态文件: /app/data/mainline_confirm_history.json {"dates": {"20260904": ["消费/内需", ...], ...}}
         由 mainline_gate.py 每日 ensure_history() 追加; 当前已 backfill 0904/0908。
用法(执行层): chain_qualified(chain_name, upto_date) / ts_qualified(ts_code, upto_date) / themes_of_ts(ts)
"""
import os, json
DATA = os.environ.get('DATA_DIR', '/app/data')
HIST_FILE = os.path.join(DATA, 'mainline_confirm_history.json')
LOOKBACK_DAYS = 40          # 主升确认资格回看窗(交易日)

def load_hist():
    try:
        return json.load(open(HIST_FILE, encoding='utf-8'))
    except Exception:
        return {'dates': {}}

def ensure_history(rows, date8):
    """mainline_gate 每日收尾: 记录 date8 当日 confirmed_candidate 主题集"""
    hist = load_hist()
    confirmed = sorted({r['theme'] for r in rows if r.get('verdict') == 'confirmed_candidate'})
    dates = hist.setdefault('dates', {})
    if date8 in dates and dates[date8] == confirmed:
        return 0
    dates[date8] = confirmed
    json.dump(hist, open(HIST_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return len(confirmed)

def _days_upto(upto_date):
    """concept_long 交易日(≤ upto_date) —— 供回看窗口"""
    try:
        raw = json.load(open(os.path.join(DATA, 'concept_long.json'), encoding='utf-8'))
        series = raw.get('series', {})
        v = next(iter(series.values()))
        return [d for d in v['dates'] if d <= upto_date]
    except Exception:
        return None

def _themes_confirmed_in_window(hist, upto_date):
    """近 LOOKBACK_DAYS 交易日内的确认主题集"""
    days = _days_upto(upto_date)
    if days:
        window = set(days[-LOOKBACK_DAYS:])
    else:
        # fallback: 日历 75 天近似
        import datetime as _dt
        u = _dt.date(int(upto_date[:4]), int(upto_date[4:6]), int(upto_date[6:]))
        window = set()
        for d in hist.get('dates', {}):
            try:
                dd = _dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
                if (u - dd).days <= 75: window.add(d)
            except Exception: pass
    out = set()
    for d in hist.get('dates', {}):
        if d in window or (days is None and d in window):
            out.update(hist['dates'][d])
    return sorted(out)

def theme_of_chain(chain):
    from fusion_mainline import THEME_CONCEPTS
    for th, cons in THEME_CONCEPTS.items():
        if chain in cons:
            return th
    return None

def chain_qualified(chain, upto_date=None):
    """执行层低吸资格: 链所属主题近 LOOKBACK 日内曾 confirmed_candidate"""
    from datetime import date as _d
    upto_date = upto_date or _d.today().strftime('%Y%m%d')
    th = theme_of_chain(chain)
    if th is None:
        return {'ok': False, 'theme': None, 'reason': 'chain_not_in_theme'}
    hist = load_hist()
    conf = _themes_confirmed_in_window(hist, upto_date)
    ok = th in conf
    return {'ok': ok, 'theme': th, 'confirmed_in_window': conf,
            'reason': None if ok else 'theme_not_recently_confirmed'}

def themes_of_ts(ts_code, concepts_map=None):
    """个股 -> 所属 15 主题(经概念成分)"""
    from fusion_mainline import THEME_CONCEPTS
    if concepts_map is None:
        import sqlite3
        db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
        concepts_map = {}
        for (ts, cn) in db.execute('SELECT ts_code, concept_name FROM stock_concept_map'):
            concepts_map.setdefault(str(ts), []).append(str(cn))
    themes = set()
    for c in concepts_map.get(ts_code, []):
        t = theme_of_chain(c)
        if t: themes.add(t)
    return sorted(themes)

def ts_qualified(ts_code, upto_date=None, concepts_map=None):
    """个股级准入: 任一所属主题近窗内曾确认"""
    from datetime import date as _d
    upto_date = upto_date or _d.today().strftime('%Y%m%d')
    ths = themes_of_ts(ts_code, concepts_map)
    if not ths:
        return {'ok': False, 'themes': [], 'reason': 'no_theme'}
    hist = load_hist()
    conf = _themes_confirmed_in_window(hist, upto_date)
    hit = [t for t in ths if t in conf]
    return {'ok': bool(hit), 'themes': ths, 'confirmed_in_window': conf,
            'hit_themes': hit, 'reason': None if hit else 'theme_not_recently_confirmed'}


def gate_top_themes(n=3, upto=None):
    """最近(<=upto) mainline_gate json rows 按 verdict 权重+heat_rank 排序前 n 主题;
    无 gate 文件返回 None。供 stock_confirm/switch_builder 等旧 fusion TOP 消费方切换到新主线判定"""
    import glob as _g, time as _t
    upto = upto or _t.strftime('%Y%m%d')
    best = None; bd = ''
    for f in _g.glob(os.path.join(DATA, 'mainline_gate_*.json')):
        d = os.path.basename(f)[14:22]
        if d <= upto and d > bd:
            bd = d; best = f
    if not best: return None
    try:
        g = json.load(open(best, encoding='utf-8'))
    except Exception:
        return None
    rows = [r for r in g.get('rows', []) if r.get('verdict') in ('confirmed_candidate', 'watch', 'reserve')]
    w = {'confirmed_candidate': 0, 'watch': 1, 'reserve': 2}
    rows.sort(key=lambda r: (w.get(r.get('verdict'), 3), r.get('heat_rank') or 99))
    return [r['theme'] for r in rows[:n]], bd

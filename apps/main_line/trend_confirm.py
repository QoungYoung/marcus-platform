# -*- coding: utf-8 -*-
"""trend_confirm.py — 主升结构确认(纯规则, v0 2026-09-08)
Wolf 判据规则化: 上升中继结构 = 低点抬高 + 2浪回调不破前低 + 再创新高。
- 输入: concept_hist.json(521东财概念, 250日) + THEME_CONCEPTS(15主题, 探测100%精确匹配)
- 双轨: A=主题等权pct合成指数判定; B=概念级分别判定按比例聚合
- 置信分层: full_window_confirmed / suspect(形态不全) / not_confirmed / window_limited(历史不足,不硬判)
- 所有阈值集中 TREND_CFG(默认值待 wolf_labels 网格标定固化, 支持 --params 覆盖)
用法: python3 trend_confirm.py [--params /app/data/trend_confirm_params.json] [--json out.json]
"""
import sys, os, json
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

TREND_CFG = {
    "new_high_window": 60,        # 确认: 收盘创近 N 日新高
    "confirm_recency_days": 5,    # 新高须发生在最近几天内(避免滞后确认)
    "pullback_max_pct": 0.18,     # H1->L2 回撤上限(健康2浪深度)
    "pullback_min_pct": 0.03,     # 回撤下限(过浅不算浪)
    "pullback_gap_days_max": 90,  # H1 与新高最大间隔(2浪时长)
    "prior_low_window": 120,      # L1(前低) 回溯窗口
    "break_ratio": 0.98,          # L2 >= L1*break_ratio 视为未破前低(收盘口径, 留3%容差可标定)
    "swing_k": 5,                 # 局部极值齿距(日)
    "theme_pass_ratio": 0.5,      # B轨: 概念级 confirmed 比例门槛
    "min_days": 120,              # 最少可用日(不足=window_limited)
}


def fill_series(close, max_none=0.08):
    """前值填充 None/NaN; None 占比超 max_none 返回 None(弃用)"""
    vals = []
    for x in close:
        if x is None or (isinstance(x, float) and x != x):
            vals.append(None)
        else:
            try: vals.append(float(x))
            except Exception: vals.append(None)
    n = len(vals)
    miss = sum(1 for v in vals if v is None)
    if miss and miss / max(n, 1) > max_none: return None
    # 开头缺失用首个有效值回填
    fv = next((v for v in vals if v is not None), None)
    if fv is None: return None
    prev = fv; out = []
    for v in vals:
        if v is None: out.append(prev)
        else: prev = v; out.append(v)
    return out

def _load_params(cfg, path):
    if path and os.path.exists(path):
        p = json.load(open(path, encoding='utf-8'))
        if isinstance(p, dict):
            src = p.get('params') if isinstance(p.get('params'), dict) else p
            cfg = dict(cfg); cfg.update({k: v for k, v in src.items() if k in cfg})
            print('params override', {k: cfg.get(k) for k in ('new_high_window', 'confirm_recency_days',
                                                              'swing_k', 'break_ratio', 'pullback_max_pct')}, flush=True)
    return cfg

def swings(c, k):
    """局部极值: 返回 lows/highs 列表 [(idx, px)] (内部不重叠: 相邻同型跳过)"""
    n = len(c); lows, highs = [], []
    for i in range(k, n - k):
        w = c[i - k:i + k + 1]
        if c[i] == min(w): lows.append((i, float(c[i])))
        if c[i] == max(w): highs.append((i, float(c[i])))
    # 极值去重(平台): 相邻低点取更低, 相邻高点取更高
    def dedup(pts, is_low):
        out = []
        for p in pts:
            if out and (p[0] - out[-1][0]) <= k:
                if (is_low and p[1] < out[-1][1]) or (not is_low and p[1] > out[-1][1]):
                    out[-1] = p
            else:
                out.append(p)
        return out
    return dedup(lows, True), dedup(highs, False)

def judge_series(close, cfg, name=''):
    """单序列结构判定 -> stage 分层 + 形态明细"""
    c = fill_series(close)
    if c is None:
        return {'name': name, 'n': 0, 'stage': 'insufficient', 'new_high': False,
                'pullback': None, 'no_break': None, 'low_higher': None, 'reason': ['数据缺失>8%']}
    n = len(c)
    out = {'name': name, 'n': n, 'date_to': '', 'stage': 'insufficient',
           'new_high': False, 'pullback': None, 'no_break': None,
           'low_higher': None, 'reason': []}
    if n < cfg['min_days']:
        out['stage'] = 'window_limited'; out['reason'].append('min_days不足')
        return out
    last = n - 1
    # 新高: 最近 recency 日内是否收盘创 new_high_window 新高(从近到远找最近一次)
    t_new = None
    for i in range(last, max(last - cfg['confirm_recency_days'], 0) - 1, -1):
        w0 = max(0, i - cfg['new_high_window'])
        if i >= w0 + 10 and c[i] >= max(c[w0:i]) * 1.0001:
            t_new = i; break
    if t_new is None:
        out['stage'] = 'not_confirmed'; out['reason'].append('近%d日无创%d日新高' % (cfg['confirm_recency_days'], cfg['new_high_window']))
        return out
    out['new_high'] = True
    if t_new < cfg['new_high_window']:
        out['stage'] = 'window_limited'; out['reason'].append('新高在序列头部,历史不足')
        return out
    lows, highs = swings(c, cfg['swing_k'])
    # H1 = t_new 前最近的 swing high
    h1 = None
    for (i, px) in reversed(highs):
        if i < t_new and px < c[t_new] and (t_new - i) <= cfg['pullback_gap_days_max'] and px > c[t_new] * (1 - cfg['pullback_max_pct'] - 0.05):
            h1 = (i, px); break
    pullback = None
    if h1 is None:
        # 允许非swing框架: 直接用 t_new 前区间最高点
        seg = c[max(0, t_new - cfg['pullback_gap_days_max']):t_new]
        if seg:
            i0 = max(0, t_new - cfg['pullback_gap_days_max'])
            hi = max(seg); idx_h = i0 + seg.index(hi)
            if hi < c[t_new] and c[t_new] > hi:
                h1 = (idx_h, hi)
    if h1:
        seg2 = c[h1[0] + 1:t_new]
        if seg2:
            lo = min(seg2)
            # 若最低点紧贴 H1(未形成回落)则放宽找最近 swing low
            l2_idx = h1[0] + 1 + seg2.index(lo)
            pct = (h1[1] - lo) / h1[1] if h1[1] > 0 else 0
            gap = t_new - h1[0]
            if cfg['pullback_min_pct'] <= pct <= cfg['pullback_max_pct'] and gap <= cfg['pullback_gap_days_max']:
                pullback = {'h1_idx': h1[0], 'h1': round(h1[1], 3), 'l2_idx': l2_idx,
                            'l2': round(lo, 3), 'pct': round(pct, 4), 'gap_days': gap}
                out['pullback'] = pullback
    # 前低 L1 + 不破判定
    if pullback:
        l2_i = pullback['l2_idx']; l2_px = pullback['l2']
        lows_b = [p for p in lows if p[0] < l2_i and p[0] >= l2_i - cfg['prior_low_window']]
        l1 = lows_b[-1] if lows_b else None
        if l1:
            no_break = l2_px >= l1[1] * cfg['break_ratio']
            out['no_break'] = bool(no_break)
            out['low_higher'] = bool(l2_px > l1[1])
            out['l1'] = {'idx': l1[0], 'px': round(l1[1], 3)}
            if no_break:
                out['stage'] = 'confirmed'
            else:
                out['stage'] = 'not_confirmed'
                out['reason'].append('2浪低点跌破前低(破位收盘口径)')
        else:
            out['stage'] = 'window_limited'
            out['reason'].append('前低窗口无swing low(历史不足)')
    elif out['new_high']:
        out['stage'] = 'suspect'
        out['reason'].append('创新高但无合格2浪回调(V反/平台突破,形态不全)')
    out['date_to'] = ''
    return out

def load_by_name(path):
    """concept_hist(BK码->{name,dates,close}) 或 concept_long({series:{name:{dates,close}}}) -> {name:{dates,close}}"""
    raw = json.load(open(path, encoding='utf-8'))
    if isinstance(raw, dict) and 'series' in raw:
        return {k: {'dates': v['dates'], 'close': v['close']} for k, v in raw['series'].items()}
    return {v.get('name', ''): v for v in raw.values()}

def theme_index(hist_names, theme_concepts):
    """轨A: 主题等权 pct 合成指数(以各自序列起点为基准)"""
    pcts = []
    for v in hist_names:
        cc = fill_series(v['close'])
        if cc is None: continue
        base = cc[0]
        if base <= 0: continue
        pcts.append([(x / base - 1.0) * 100.0 for x in cc])
    if not pcts: return None, None
    n = len(pcts[0])
    idx = [100.0 + sum(p[i] for p in pcts) / len(pcts) for i in range(n)]
    return idx, (v['dates'] if 'dates' in v else None)

def main():
    args = sys.argv[1:]
    params_p = None; json_p = None; hist_p = None
    for i, a in enumerate(args):
        if a == '--params' and i + 1 < len(args): params_p = args[i + 1]
        if a == '--json' and i + 1 < len(args): json_p = args[i + 1]
        if a == '--hist' and i + 1 < len(args): hist_p = args[i + 1]
    cfg = _load_params(dict(TREND_CFG), params_p)
    hist_p = hist_p or os.path.join(DATA, 'concept_hist.json')
    from fusion_mainline import THEME_CONCEPTS
    by_name = load_by_name(hist_p)
    themes_out = []
    date_to = max((v.get('dates') or [''])[-1] for v in by_name.values() if v.get('dates'))
    for th, cons in THEME_CONCEPTS.items():
        vs = [by_name[c] for c in cons if c in by_name]
        # 轨A 主题指数
        idxA, datesA = theme_index(vs, cons)
        judgeA = judge_series(idxA, cfg, th + '指数') if idxA else {'stage': 'insufficient', 'reason': ['无成分']}
        # 轨B 概念级
        per = [judge_series(v['close'], cfg, v.get('name', '')) for v in vs]
        cnt = {'confirmed': 0, 'suspect': 0, 'not_confirmed': 0, 'window_limited': 0, 'insufficient': 0}
        for j in per: cnt[j['stage']] = cnt.get(j['stage'], 0) + 1
        # 分母=可判池(数据缺失/历史不足不算结构未确认)
        judgeable = ['confirmed', 'suspect', 'not_confirmed']
        total = sum(cnt[s] for s in judgeable)
        ratio = round(cnt['confirmed'] / total, 3) if total > 0 else 0
        stageB = ('confirmed' if total > 0 and ratio >= cfg['theme_pass_ratio']
                  else 'suspect' if cnt['suspect'] > 0 and total == 0 else
                  'suspect' if cnt['suspect'] > 0 and cnt['confirmed'] == 0 else
                  'not_confirmed' if total > 0 else 'insufficient')
        _gate_cfg = None
        try:
            _gp = json.load(open(os.path.join(DATA, 'trend_gate_params.json'), encoding='utf-8'))
            _gate_cfg = (_gp.get('chosen') or {}).get('mode'), (_gp.get('chosen') or {}).get('t')
        except Exception:
            pass
        gate = None
        if _gate_cfg and _gate_cfg[0] and total > 0:
            m, t = _gate_cfg
            b = ratio >= t
            a = judgeA.get('stage') == 'confirmed'
            gate = {'A_only': a, 'B_only': b, 'A_or_B': a or b, 'A_and_B': a and b}.get(m)
        themes_out.append({'theme': th, 'date_to': date_to, 'gate': gate, 'gate_rule': ((_gate_cfg[0] or '') + '_t' + str(_gate_cfg[1])) if _gate_cfg and _gate_cfg[0] else None,
                           'track_a': {'stage': judgeA['stage'], 'reason': judgeA.get('reason', []),
                                       'new_high': judgeA.get('new_high'), 'pullback': judgeA.get('pullback'),
                                       'no_break': judgeA.get('no_break'), 'l1': judgeA.get('l1')},
                           'track_b': {'confirmed_n': cnt['confirmed'], 'judgeable': total, 'total': len(per),
                                       'ratio': ratio, 'stage': stageB, 'counts': cnt},
                           'agreement': judgeA['stage'] == stageB})
        gt = 'PASS' if gate else 'FAIL'
        gr = ('[' + (_gate_cfg[0] if _gate_cfg and _gate_cfg[0] else '?') + ' t=' + str((_gate_cfg or (None, None))[1]) + ']') if gate is not None else ''
        print('%-12s A=%-9s B=%s(%d/%d) GATE=%-4s %s' % (
            th, judgeA['stage'], stageB, cnt['confirmed'], total, (gt if gate is not None else 'n/a'), gr), flush=True)
    out = {'generator': 'trend_confirm_v0', 'date_to': date_to,
           'data_lag_notice': 'concept_hist 截至 ' + date_to + '(若晚于交易日需先更新)',
           'cfg': cfg, 'themes': themes_out}
    p = json_p or os.path.join(DATA, 'trend_confirm_' + date_to + '.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE', p)

if __name__ == '__main__':
    main()

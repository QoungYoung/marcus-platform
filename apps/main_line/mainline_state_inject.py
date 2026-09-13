# -*- coding: utf-8 -*-
"""mainline_state_inject.py — 把 mainline_gate 摘要+主题波浪结构注入 main_line_state.json(方案①)
Pi(auto_trade 会话)每次读 main_line_state.json 即自动看到: confirmed_candidate/watch/reserve 列表,
每主题 heat_rank/GATE/结构比例/verdict/波浪结构文本(2浪回调低+前低, 3浪健康)。
只增 'mainline_gate' 字段, 不覆盖原 catalyst/main_line 逻辑。用法: python3 mainline_state_inject.py <date8>
"""
import sys, os, json
sys.path.insert(0, '/app')
DATA = os.environ.get('DATA_DIR', '/app/data')

def _inject(ms, date8):
    ms_p = os.path.join(DATA, 'main_line_state.json')
    gate_p = os.path.join(DATA, 'mainline_gate_%s.json' % date8)
    trend_p = os.path.join(DATA, 'trend_confirm_%s_long.json' % date8)
    if not os.path.exists(gate_p):
        print('no gate json', gate_p); return 1
    gate = json.load(open(gate_p, encoding='utf-8'))
    trend = None
    if os.path.exists(trend_p):
        trend = json.load(open(trend_p, encoding='utf-8'))
    # 参考日期序列(idx->date)
    dref = None
    try:
        cl = json.load(open(os.path.join(DATA, 'concept_long.json'), encoding='utf-8'))
        v = next(iter(cl.get('series', {}).values()))
        dref = v.get('dates')
    except Exception:
        pass
    def dt(idx):
        try:
            if dref and isinstance(idx, int) and 0 <= idx < len(dref): return dref[idx]
        except Exception: pass
        return idx
    trend_by_theme = {}
    if trend:
        for t in trend.get('themes', []):
            trend_by_theme[t['theme']] = t
    themes = []
    for r in gate.get('rows', []):
        if r.get('verdict') not in ('confirmed_candidate', 'watch', 'reserve'):
            continue
        tr = trend_by_theme.get(r['theme'], {})
        ta = tr.get('track_a') or {}
        struct = None
        if ta.get('stage') == 'confirmed':
            pb = ta.get('pullback') or {}
            l1 = ta.get('l1') or {}
            if pb and l1:
                struct = ('3浪健康: 2浪回调低点 %.2f(%s), 未破前低 %.2f(%s), 再创新高'
                          % (pb.get('l2') or 0, dt(pb.get('l2_idx')), l1.get('px') or 0, dt(l1.get('idx'))))
                # 浪级标注(2026-09-09): 近似 3 浪目标 = 2浪低 + 1.618*(前高-1浪前低)
                h1 = pb.get('h1') or 0
                w1 = max(h1 - (l1.get('px') or 0), 0.0)
                if w1 > 0:
                    tgt = (pb.get('l2') or 0) + 1.618 * w1
                    cur_wave = ta.get('new_high')
                    struct += ' | 主升3浪运行中, 3浪目标≈%.1f(1.618x1浪% .1f)' % (tgt, w1)
                    _topic_wave = {'wave': 3, 'w1_len': round(w1, 2), 'w2_low': pb.get('l2'),
                                   'target1618': round(tgt, 1), 'basis_date': dt(pb.get('l2_idx'))}
            elif pb:
                struct = '主升结构确认(回调低点 %.2f@%s)' % (pb.get('l2') or 0, dt(pb.get('l2_idx')))
        elif ta.get('stage') == 'suspect' and ta.get('new_high'):
            struct = '创新高但2浪形态不全(疑似V反/平台), 等结构确认'
        themes.append({'theme': r['theme'], 'heat_rank': r.get('heat_rank'), 'heat_score': r.get('heat_score'),
                       'gate': r.get('gate'), 'structure_ratio': r.get('gate_ratio'), 'judgeable': r.get('judgeable'),
                       'verdict': r.get('verdict'), 'wave_structure': struct})
    summary = {'date': date8, 'gate_rule': gate.get('gate_rule'),
               'confirmed_candidate': [t['theme'] for t in themes if t['verdict'] == 'confirmed_candidate'],
               'watch': [t['theme'] for t in themes if t['verdict'] == 'watch'],
               'reserve': [t['theme'] for t in themes if t['verdict'] == 'reserve'],
               'themes': themes,
               'wave_env': gate.get('wave_env'),
               'note': ('由 mainline_gate_daily 注入: 主线资格=曾确认(40交易日)∩结构GATE; '
                        'wave_env 仅风险提示(破 c_kill 执行层撤主线), 不否决资格; '
                        '254低吸触发=回调不破前低(dip_prev_low), 浪位参考 wave_structure')}
    if ms is None:
        ms = {}
    ms['mainline_gate'] = summary
    if isinstance(ms, dict) and ms:
        json.dump(ms, open(ms_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('INJECTED main_line_state.json mainline_gate date', date8,
          '| confirmed', summary['confirmed_candidate'], '| watch', summary['watch'], flush=True)
    return 0

def _inject_select(ms, date8):
    """把**方向层主线（池判定）**注入 main_line_state.json 的 mainline_select 字段（2026-09-13 起为主）。

    来源 = wolf_mainline_select.json（由 jobs/wolf_mainline_select.py 盘后产出，含池/选中/浪型）。
    它取代原先 mainline_gate 的"主线判定"角色；gate 注入默认关闭（WOLF_INJECT_GATE=1 可回退）。
    """
    p = os.path.join(DATA, 'wolf_mainline_select.json')
    if not os.path.exists(p):
        print('no wolf_mainline_select.json', p)
        return 1
    try:
        st = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print('read fail', e)
        return 1
    if not st.get('mainline'):
        print('mainline_select 无主线（未启用或历史不足）')
        return 1
    summary = {'date': st.get('date') or date8,
               'mainline': st.get('mainline'), 'second': st.get('second'),
               'pool': st.get('pool') or [], 'pool_top': st.get('pool_top') or [],
               'pool_k': st.get('pool_k'), 'pool_share5': st.get('pool_share5') or {},
               'candidates': st.get('rank_in_gate') or [], 'r5': st.get('r5') or {},
               'themes': st.get('themes') or [], 'use_gate': st.get('use_gate'),
               'note': '方向层主线 = 池(主题近5日成交额占比 topK ∩ 近5日相对强度>0) → 池内 r5 top1；'
                       '与他股票主线层对齐 recall 75%/top1 54%/top3 85%；gate 默认不参与选择'}
    if ms is None:
        ms = {}
    ms['mainline_select'] = summary
    # 2026-09-13：默认让旧的 gate 摘要**彻底退场**——从 state 里移除该键（回退时 WOLF_INJECT_GATE=1 会重新注入）
    if os.getenv('WOLF_INJECT_GATE', '0').strip().lower() not in ('1', 'true', 'yes', 'on'):
        ms.pop('mainline_gate', None)
    # 2026-09-13：judge 任务停用（研报线关闭 + 主线改由方向层直写）→ catalyst/fusion **无人刷新**，
    # 从 state 里移除，避免消费方读到过期值（stock_confirm_judge 会自然落到 main_line）。
    if os.getenv('WOLF_KEEP_CATALYST_FUSION', '0').strip().lower() not in ('1', 'true', 'yes', 'on'):
        ms.pop('catalyst', None)
        ms.pop('catalyst_source', None)
        ms.pop('fusion', None)
    json.dump(ms, open(os.path.join(DATA, 'main_line_state.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('INJECTED main_line_state.json mainline_select date', summary['date'],
          '| mainline', summary['mainline'], '| pool', summary['pool'], flush=True)
    return 0


def main():
    date8 = sys.argv[1] if len(sys.argv) > 1 else None
    if not date8:
        import time
        date8 = time.strftime('%Y%m%d')
    ms_p = os.path.join(DATA, 'main_line_state.json')
    ms = None
    try:
        ms = json.load(open(ms_p, encoding='utf-8'))
    except Exception:
        ms = {}
    # 2026-09-13：方向层主线（池判定）为主；gate 注入默认关闭（WOLF_INJECT_GATE=1 回退）
    rc = _inject_select(ms, date8)
    if os.getenv('WOLF_INJECT_GATE', '0').strip().lower() in ('1', 'true', 'yes', 'on'):
        _inject(ms, date8)
    else:
        print('SKIP gate 注入（WOLF_INJECT_GATE=0，方向层主线已取代其判定角色）', flush=True)
    return rc

if __name__ == '__main__':
    sys.exit(main())

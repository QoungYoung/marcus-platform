# -*- coding: utf-8 -*-
"""daily_inputs_chain.py — 主线确认每日更新链(2026-09-09 接入调度)
steps(盘后, 依次): build_concept_long(若未到当日) -> trend_confirm(结构GATE as-of当日)
  -> (heat_v2 / mainline_gate 步骤已于 2026-09-13 **删除**；主线判定唯一＝方向层池判定)
产物: concept_long.json / trend_confirm_{date}_long.json / heat_v2_{date}.json / mainline_gate_{date}.json / mainline_confirm_history.json
用法: python3 mainline_gate_daily.py [--date YYYYMMDD]
"""
import os, sys, json, subprocess, time
sys.path.insert(0, '/app')
DATA = os.environ.get('DATA_DIR', '/app/data')
APP = '/app/apps/main_line'

def sh(step, cmd):
    print('[%s] %s' % (step, ' '.join(cmd)), flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or '').strip()
    err = (r.stderr or '').strip()
    if out: print(out[-2500:], flush=True)
    if r.returncode != 0:
        print('[%s] FAILED rc=%s\n%s' % (step, r.returncode, err[-1500:]), flush=True)
        sys.exit(r.returncode)
    print('[%s] ok %.0fs' % (step, time.time() - t0), flush=True)

def main():
    import time as _t
    date8 = _t.strftime('%Y%m%d')
    for i, a in enumerate(sys.argv[1:]):
        if a == '--date': date8 = sys.argv[i + 2]
    py = sys.executable
    # 1) concept_long 增量(未到当日才全量)
    try:
        cl = json.load(open(os.path.join(DATA, 'concept_long.json'), encoding='utf-8'))
        end = (cl.get('meta') or {}).get('end')
        if end and end >= date8:
            print('[build] concept_long 已到 %s, skip' % end, flush=True)
        else:
            sh('build_concept_long', [py, os.path.join(APP, 'build_concept_long.py'), '20250101'])
    except Exception as e:
        print('[build] 缺 concept_long, 全量构建:', str(e)[:80], flush=True)
        sh('build_concept_long', [py, os.path.join(APP, 'build_concept_long.py'), '20250101'])
    # 1.5) ETF 份额流(通道弱佐证, 份额加权; ~30s)
    sh('build_etf_flow', [py, os.path.join(APP, 'build_etf_flow.py'), '--date', date8])
    # 1.6) 龙虎榜机构/游资净买(修正窗口, ~15s)
    sh('build_inst_flow', [py, os.path.join(APP, 'build_inst_flow.py'), '--date', date8])
    # 2) 结构 GATE
    sh('trend_confirm', [py, os.path.join(APP, 'trend_confirm.py'), '--hist', os.path.join(DATA, 'concept_long.json'),
                         '--params', os.path.join(DATA, 'trend_confirm_params.json'), '--as-of', date8,
                         '--json', os.path.join(DATA, 'trend_confirm_%s_long.json' % date8)])
    # 3) heat_v2 步骤与 4) mainline_gate 步骤 **已于 2026-09-13 删除**（不是停用）：
    #    · heat_v2：他 doing 主题的平均 heat 排名 10.42/13（随机 7.0）→ −3.42, t=−11.89 ⇒ 假设与做法相反；
    #    · mainline_gate：单独命中他方向 ≈ 随机（top1 28%/precision 12%），作约束净负。
    #    主线判定唯一 = 方向层 wolf_mainline_select（池：量能占比 topK ∩ r5>0 → 池内 r5 top1）；
    #    历史产物 heat_v2_*.json / mainline_gate_*.json 保留（仅归档与分析脚本用）。
    # 5) 注入 main_line_state.json(Pi 会话可见: gate 摘要+波浪结构)
    sh('inject_state', [py, os.path.join(APP, 'mainline_state_inject.py'), date8])
    print('MAINLINE_GATE_DAILY DONE', date8, flush=True)

if __name__ == '__main__':
    main()

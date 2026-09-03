# -*- coding: utf-8 -*-
"""consolidate_recent_sim.py — 汇总 LLM 版最近两周模拟 (wave_agent + main_line_judge)
读 data/wave_state_2026-<d>.json + data/main_line_state_2026-<d>.json, 结合宏观as-of与T回放, 逐日产出模块判定表。
"""
import os, sys, json, math
DATA=os.environ.get('DATA_DIR','/app/data')
sys.path.insert(0,'/app/app')
try:
    from app.services.trade_graph import _wave_norm, _wave_operation, _OP_TO_GATE
except Exception:
    def _wave_norm(v): return (str(v or '').strip().lower().replace('_',' ').replace(' ',''))
    def _wave_operation(level, sub): return 'side'
    _OP_TO_GATE={'build':'normal','t_only':'t_only','side':'side','defense':'defense','exit':'exit'}
def load(p):
    try: return json.load(open(os.path.join(DATA,p),encoding='utf-8'))
    except: return None
def g(op): return _OP_TO_GATE.get(op,op)
DATES=['2026-08-20','2026-08-21','2026-08-24','2026-08-25','2026-08-26','2026-08-27','2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03']
# macro as-of 08-12 switches (窗口内无日档, 用最近近似)
mac=load('macro_state_history.json')
sw=((mac or {}).get('states') or {}).get('2026-08-12',{}).get('macro_switches',{})
flags=sorted(sw.get('flags') or [])
print('macro as-of 08-12 flags:',flags)
print()
print('%-12s %-5s %-8s %-9s %-10s %-10s' % ('date','wave','sub','op','gate','main_line'))
for d in DATES:
    w=load('wave_state_'+d+'.json')
    m=load('main_line_state_'+d+'.json')
    lvl=w.get('level','?') if w else '?'
    sub=w.get('sub_level','') if w else ''
    op=_wave_operation(lvl,sub) if w else '?'
    gate=g(op) if w else '?'
    ml=(m or {}).get('main_line','?')
    print('%-12s %-5s %-8s %-9s %-10s %-10s' % (d, lvl, sub, op, gate, ml))
print()
print('== P2 Gate 后果(按 macro as-of) ==')
print('  margin_burst(硬拦新建仓):', 'margin_burst' in flags)
print('  gjd_withdraw(软降0.5):', 'gjd_withdraw' in flags)
print('  lhb_foreign_sell(方向不追):', 'lhb_foreign_sell' in flags)

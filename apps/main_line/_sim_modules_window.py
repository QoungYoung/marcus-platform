# -*- coding: utf-8 -*-
"""sim_modules_window.py — 用已落地模块离线模拟最近两周判定(无LLM, 走规则回退)
- 浪型: wave_level.judge_wave(date) -> label -> operation/gate (t_only只做T等)
- 主线: 用 main_line_judge 08-31 agent 输出(main_line=AI/算力/科技) + confirm_chain.mainline_act 逐日确认
- 位置: 按近60个交易日价格分位(用能凑到的日线; 不足则UNKNOWN)
输出: /app/data/recent_sync/sim_modules.json
"""
import os, sys, json
DATA='/app/data'
sys.path.insert(0,'/app/apps'); sys.path.insert(0,'/app/app')
from main_line import wave_level

DATELIST=['2026-08-20','2026-08-21','2026-08-24','2026-08-25','2026-08-26','2026-08-27','2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03']
OPS={'主升浪':'build','反弹(B反)':'t_only','调整(下杀)':'defense','震荡/待明确':'side'}
GATE={'build':'normal','t_only':'t_only','side':'side','defense':'defense'}
res=[]
for d in DATELIST:
    lbl,f=wave_level.judge_wave(d)
    op=OPS.get(lbl,'side'); gate=GATE.get(op,op)
    res.append({'date':d,'wave_label':lbl,'operation':op,'gate':gate,
                'features': (f if f else {})})
    print(d, lbl, 'op=',op,'gate=',gate,
          'r20=',round((f.get('r20') or 0)*100,1),'% r60=',round((f.get('r60') or 0)*100,1),
          '% dd=',round((f.get('dd') or 0)*100,1),'%',flush=True)
json.dump({'module':'wave_level_sim','dates':res}, open('/app/data/recent_sync/sim_modules.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('WROTE sim_modules.json',flush=True)

# -*- coding: utf-8 -*-
import sys; sys.path.insert(0,'/app')
from app.services import trade_graph as tg
def chk(name, s, bad):
    hit=[b for b in bad if b in s]
    print('%-34s 平台残留=%s' % (name, hit or '无'))
ti_osc=tg._get_trade_instruction('mid_morning','oscillation')
ti_trd=tg._get_trade_instruction('late_morning','trend')
sty=tg._get_style_strategy({'style_regime':'OFFENSE','consecutive_days':3,'suggestion':'x'})
reg=tg._get_regime_strategy('trend')
for nm,s in [('instruction_osc',ti_osc),('instruction_trend',ti_trd),('style_OFFENSE',sty),('regime_trend',reg)]:
    chk(nm, s, ['9:35','9:50','10:35','60分钟','MA5 > MA20','单票10-15%','产业链建仓 | **禁用**','1:3','单票仓位 | ≤5-8%'])
print('instruction_osc 含[狼大买点]:', '狼大买点' in ti_osc)
print('regime_trend 含[狼大口径]:', '狼大口径' in reg)

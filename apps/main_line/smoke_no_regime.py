# -*- coding: utf-8 -*-
import sys; sys.path.insert(0,'/app')
from app.services import trade_graph as tg
# regime_context 应为空
print('regime_context 注入值(应空):', repr(tg.node_fetch_context({'execution_id':'x','task_id':'t','window':'mid_morning'}).get('regime_context')))
# trade_instruction 不再按震荡/趋势分流
ti=tg._get_trade_instruction('mid_morning','oscillation')
print('instruction 含[震荡市/趋势市状态]:', ('震荡市' in ti and '只买不卖' in ti) or ('趋势市' in ti and '10:35' in ti))
print('instruction 含[按浪型/主线]:', '按浪型/主线' in ti, '| 含[狼大买点]:', '狼大买点' in ti)
print('--- instruction 首行 ---')
print(ti.split(chr(10))[0])

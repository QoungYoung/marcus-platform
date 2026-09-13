# -*- coding: utf-8 -*-
import sys; sys.path.insert(0,'/app')
from app.services import trade_graph as tg
s=tg._get_regime_strategy('oscillation')
print('含[产业链建仓禁用]:', '产业链建仓 | **禁用**' in s)
print('含[60分钟辅助]:', "60分钟辅助" in s)
print('含[狼大买点]:', '狼大买点' in s)
print('--- 抽查 ---')
for line in s.splitlines():
    if any(k in line for k in ('做T仓','产业链/主线建仓','做T周期')): print(' ', line.strip())

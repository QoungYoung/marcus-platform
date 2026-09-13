# -*- coding: utf-8 -*-
import sys; sys.path.insert(0,'/app')
from app.services import trade_graph as tg
try:
    c = tg._read_main_line_context()
    print('含[产业链形态]:', '产业链形态' in c)
    print('含[get_concept_mapping]:', 'get_concept_mapping' in c)
    print('含[上中下游/软硬]:', '上中下游' in c and '软硬' in c)
    print('含[chain_state]数据注入:', 'chain_state' in c)
    for line in c.split(chr(10)):
        if '产业链形态' in line: print('  ', line.strip()[:120])
except Exception as e:
    print('ERR', e)

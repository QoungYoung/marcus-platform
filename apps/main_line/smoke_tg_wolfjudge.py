# -*- coding: utf-8 -*-
import sys
sys.path.insert(0,'/app')
from app.services import trade_graph as tg
try:
    print('wolf_judge_context=', repr(tg._read_wolf_judge_context()[:120]))
except Exception as e:
    print('ERR', e)

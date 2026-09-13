# -*- coding: utf-8 -*-
import sys; sys.path.insert(0,'/app')
from app.services import trade_graph as tg
from app.services.wolf_discipline import board_half
import json, datetime
# rotation context 是否含去弱留强/龙头/相对强度
try:
    c = tg._read_rotation_gate_context()
    print('rotation 含[去弱留强]:', '去弱留强' in c, '| 含[主线龙头]:', '龙头' in c, '| 含[相对强度]:', '相对强度' in c)
    print(c.split(chr(10))[0])
except Exception as e:
    print('rotation_ctx ERR', e)
# board_half is20 xq 兼容
bh = board_half(json.dumps({"positions":[{"symbol":"SZ300460","avg_cost":10.0,"volume":1000}]}), datetime.datetime.now(),
                quotes={"SZ300460":{"current":12.5,"pre_close":10.5}})
print('board_half(SZ300460 20%板触发):', bool(bh.get('active_sells')), '| reason:', (bh.get('active_sells') or [{}])[0].get('reason','')[:40] if bh.get('active_sells') else '')
# TMonitor 有 _check_board_half
from app.services.t_monitor import TMonitor
print('TMonitor._check_board_half exists:', hasattr(TMonitor, '_check_board_half'))

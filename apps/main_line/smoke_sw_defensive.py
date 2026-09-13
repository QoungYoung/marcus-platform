# -*- coding: utf-8 -*-
import sys
sys.path.insert(0,'/app')
from app.services.wolf_t_rules import resolve_sw_sector, defensive_t_reduce_sw, _sw_daily_high
print('SW 688981=', resolve_sw_sector('688981'))
print('SW 603259=', resolve_sw_sector('603259'))
print('SW 002945=', resolve_sw_sector('002945'))
print('801080 daily keys sample=', list(_sw_daily_high('801080.SI').items())[-3:])
ok,reason = defensive_t_reduce_sw('688981', 126.78, 129.28)
print('defensive_t_reduce_sw(688981,126.78,129.28) =>', ok, '|', reason)
ok2,reason2 = defensive_t_reduce_sw('603259', 160.41, 163.40)
print('defensive_t_reduce_sw(603259,160.41,163.40) =>', ok2, '|', reason2)

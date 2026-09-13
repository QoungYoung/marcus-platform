# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0,'/app')
from app.services.wolf_judge import six_factor_score, buy_gate, gate_label, day_gate_prompt
print('day_gate_prompt(20260901)=', repr(day_gate_prompt(date='20260901')[:150]))
print('day_gate_prompt(20260903)=', repr(day_gate_prompt(date='20260903')[:150]))
for d in ['20260902']:
    best=None
    for c in ['159516','588170','301018']:
        sc,det=six_factor_score(c,d)
        if sc is None: continue
        print('  %s %s=%.2f %s' % (d,c,sc,gate_label(buy_gate(sc))))

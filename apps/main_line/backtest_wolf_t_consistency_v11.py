# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v11.py — 用生产 wolf_judge.six_factor_score 验证六因子门不伤召回"""
import os, sys, json
sys.path.insert(0, '/app')
from app.services.wolf_judge import six_factor_score, buy_gate, gate_label, THR_ALLOW, THR_BOUNDARY
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
ELIG=['159516','588170','301018']
WOLF_BUY={'20260824','20260825','20260901','20260902'}
print('%-8s %-6s | %-9s %s' % ('date','wolf_buy','max_gate','scores'))
must_not_block=[]
for d in DAYS:
    best=None
    per=[]
    for c in ELIG:
        sc, det = six_factor_score(c, d)
        if sc is None: continue
        g=buy_gate(sc)
        per.append((c, round(sc,2), gate_label(g)))
        if best is None or sc>best[0]: best=(sc, c, g)
    if best is None:
        print('%-8s %-6s | %-9s %s' % (d, 'YES' if d in WOLF_BUY else 'no', 'no_cand',''))
        if d in WOLF_BUY: must_not_block.append(d)
        continue
    g=best[2]
    flag='***' if d in WOLF_BUY else ''
    print('%-8s %-6s | %-9s %s' % (d, 'YES' if d in WOLF_BUY else 'no', g, ' '.join('%s=%.2f(%s)'%(c,s,gl) for c,s,gl in per))+' '+flag)
    if d in WOLF_BUY and g==0:
        must_not_block.append(d)
print()
print('狼大买日=', sorted(WOLF_BUY))
print('被硬拦(block)的买日=', must_not_block or '无')
print('若 must_not_block 为空 -> 六因子门(>=%s allow, %s~%s boundary) 不会伤召回' % (THR_ALLOW, THR_BOUNDARY, THR_ALLOW))
print('阈值: allow>=%s boundary>=%s' % (THR_ALLOW, THR_BOUNDARY))

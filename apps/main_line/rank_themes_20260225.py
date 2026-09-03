# -*- coding: utf-8 -*-
"""rank_themes_20260225.py — 结构优先主题打分排序 (2026-02-25)"""
import os, sys
sys.path.insert(0, '/app/apps/main_line')
import plan_compiler_v0 as pc
CANDS=[
 ('液冷(数据中心)', 'BK1138.DC', 1.0),
 ('算力', 'BK1134.DC', 1.0),
 ('数据中心', 'BK0922.DC', 1.0),
 ('半导体', 'BK0917.DC', 1.0),
 ('AI芯片', 'BK1127.DC', 1.0),
 ('存储芯片', 'BK1137.DC', 1.0),
 ('国产芯片', 'BK0891.DC', 1.0),
 ('人工智能', 'BK0800.DC', 1.0),
 ('CPO(光模块)', 'BK1128.DC', 1.0),
 ('信创', 'BK1104.DC', 1.0),
 ('人形机器人', 'BK1184.DC', 1.0),
 ('军工', 'BK0490.DC', 0.5),
 ('券商', 'BK0711.DC', 0.5),
 ('化工原料', 'BK0512.DC', 0.5),
 ('黄金', 'BK0547.DC', 0.4),
 ('创新药', 'BK1106.DC', 0.4),
]
rows=[]
for name,code,ml in CANDS:
    s=pc.theme_pit_score(code, '2026-02-25', mainline=ml)
    if s: rows.append((s['thesis_score'], name, code, s))
rows.sort(reverse=True)
print('=== 2026-02-25 结构优先主题排序 ===')
for score,name,code,s in rows:
    print('%-14s %-8s %-10s thesis=%.2f | rel=%.2f(%+.1f%%) 不拥挤=%.2f 主线=%.2f 资金=%.2f(净%.1f亿)' % (
        name, code, '', score, s['rel'], s['rel_pct'], 1-s['crowd'], s['mainline'], s['fund'], s['net20']))

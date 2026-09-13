# -*- coding: utf-8 -*-
# 批量：对狼大有浪型表态的日期跑 wave_agent, 输出 level 与狼大判定对比
import sys, json, time
sys.path.insert(0, '/tmp')
import wave_agent as wa
# (日期, 狼大判定级别, 备注)
tests=[
 ('2025-02-06','2浪调整5浪末(等待主升3浪)','狼大: 指数2浪调整末端, 后面接主升3浪, 主线AI但指数属调整'),
 ('2026-01-12','主升(3-3段)','狼大: 3-3开始了不要轻易下车(主升)'),
 ('2026-02-26','(近高位, 待判/或4-4)','狼大后期称2026下半年走4-3筑底→4-4→4-5, 彼时或处高位(3浪/4-4)'),
 ('2026-06-26','(下跌/4-5下杀)','前次 agent 判 4-5下杀; 狼大7月称4-3筑底'),
]
for d, wolf, note in tests:
    f=wa.index_features(d)
    if f is None: print(d,'no data'); continue
    prompt=wa.build_prompt(f)
    try:
        reply=wa.call_agent(prompt, session='wv'+d.replace('-',''))
        res=wa.parse(reply)
    except Exception as e:
        res={'error':str(e)[:100],'parse_failed':True}
    print('==', d, '| 狼大:', wolf)
    print('   agent level:', res.get('level'), '| sub:', res.get('sub_level'), '| conf:', res.get('confidence'), '| op:', res.get('operation'))

# -*- coding: utf-8 -*-
import json, sys, os
sys.path.insert(0,"apps/main_line")
import wave_agent as wa
# wolf 4-x 明确判定日期 -> 期望 (level,sub,op)
TRUTH=[
 ("2026-07-23","wolf: 大4回调浪的4-3筑底阶段,能走出4-4但别预期太高,一般4-5是失败5浪","d4","4-3","side"),
 ("2026-07-27","wolf: 主升4-3转4-4阶段,方向向上但幅度有限","d4","4-3/4-4","t_only"),
 ("2026-07-31","wolf: 大盘都走到4-3的底了","d4","4-3","side"),
 ("2026-08-03","wolf: 现4-2阶段,全市场只有科技涨出双头","d4","4-2","t_only"),
 ("2026-08-13","wolf: 接近4-4第一波高位,震荡再冲击,然后看是否诱多走4-5还是失败5","d4","4-4/4-5","t_only"),
]
for date,quote,el,es,eo in TRUTH:
    f=wa.index_features(date)
    if f is None: print(date,"NO_DATA"); continue
    try:
        res=wa.parse(wa.call_agent(wa.build_prompt(f), session="v4x_")); 
    except Exception as ex:
        print(date,"ERR",str(ex)[:120]); continue
    print("===", date, "| wolf:", quote[:40])
    print("   agent:", res.get("level"), res.get("sub_level"), res.get("operation"))
    print("   exp  :", el, es, eo)
    print("   reason:", str(res.get("reasons",""))[:200])

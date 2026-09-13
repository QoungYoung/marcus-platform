# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0,"apps/main_line"); import wave_agent as wa
for d in ["2026-08-31","2025-03-27","2016-04-09"]:
    f=wa.index_features(d)
    ml=f.get("main_line") or {}
    print(d, "-> main_line:", json.dumps(ml, ensure_ascii=False)[:80])

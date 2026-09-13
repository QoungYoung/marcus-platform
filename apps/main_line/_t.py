# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0,"apps/main_line"); import wave_agent as wa
for d in ["2016-04-09","2016-05-19","2025-04-09","2026-06-26"]:
    m=wa.get_market_context(d); gjd=m.get("gjd") or {}
    print("===",d,"| gjd:", json.dumps(gjd, ensure_ascii=False)[:240])

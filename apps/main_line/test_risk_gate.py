# -*- coding: utf-8 -*-
"""risk_gate v1 selftest（R-R1~R-R4 场景）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_gate as rg
from datetime import date

CASES = [
    # (name, cand, margin_pct, top_signal, day, expected)
    ("clean-allow", {"symbol":"X","flags":[]}, None, False, date(2026,9,2), "allow"),
    ("ST-block", {"symbol":"X","is_st":True}, None, False, None, "block"),
    ("立案-block", {"symbol":"X","flags":["立案"]}, None, False, None, "block"),
    ("重组终止-block", {"symbol":"X","flags":["重组终止"]}, None, False, None, "block"),
    ("业绩暴雷-block", {"symbol":"X","earnings_bad":True}, None, False, None, "block"),
    ("两融高位+破位-reduce", {"symbol":"X","flags":[]}, 0.9, True, None, "reduce"),
    ("两融高位无破位-allow", {"symbol":"X","flags":[]}, 0.9, False, None, "allow"),
    ("财报窗口高位-review", {"symbol":"X","flags":[],"high_position":True}, None, False, date(2026,4,10), "review"),
    ("财报窗口已clear-allow", {"symbol":"X","flags":[],"high_position":True,"earnings_clear":True}, None, False, date(2026,4,10), "allow"),
    ("拥挤review", {"symbol":"X","flags":[],"crowded":True}, None, False, None, "review"),
    ("非窗口高位-allow", {"symbol":"X","flags":[],"high_position":True}, None, False, date(2026,6,10), "allow"),
]
fails = 0
for name, cand, mp, ts, day, exp in CASES:
    got = rg.decide(cand, margin_pct=mp, top_signal=ts, day=day)["decision"]
    if got != exp:
        fails += 1
        print("FAIL", name, "got", got, "exp", exp)
print("PASS %d/%d FAIL %d" % (len(CASES)-fails, len(CASES), fails))
raise SystemExit(1 if fails else 0)

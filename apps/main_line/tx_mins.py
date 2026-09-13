# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "/app/scripts/p0_probe")
from data_sources import fetch_tencent_mkline
bars = fetch_tencent_mkline("sh588170", "m5", 320)
today = [b for b in bars if b["time"].startswith("20260907")]
print("today(09-07) bars:", len(today), "first", today[0]["time"] if today else None, "last", today[-1]["time"] if today else None)
if today:
    hs=[b["high"] for b in today]; ls=[b["low"] for b in today]; cs=[b["close"] for b in today]; vs=[b["vol"] for b in today]
    amt = sum(b["close"]*b["vol"] for b in today); vol = sum(vs)
    vwap = amt/vol if vol else 0
    print("today high", max(hs), "low", min(ls), "close_sofar", cs[-1], "vwap", round(vwap,4))
    prev_low = 0.926; prev_close = 0.935  # 09-03
    print("--- 今日T区间(盘中) ---")
    print("低吸位(近前低/回撤): %.3f ~ %.3f" % (round(min(prev_low*0.995, prev_low),3), round(prev_low*1.005,3)))
    print("T出位(+3~5点, 近今日高): %.3f ~ %.3f" % (round(prev_close*1.03,3), round(prev_close*1.05,3)))
    print("今日VWAP破位参考: %.3f" % round(vwap,3))
    print("破位走(前低*0.99): %.3f" % round(prev_low*0.99,3))

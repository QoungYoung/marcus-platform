# -*- coding: utf-8 -*-
import openpyxl, re
wb = openpyxl.load_workbook("/tmp/wolf_corpus.xlsx", read_only=True, data_only=True)
kw = re.compile(r"止损|清仓|减仓|破位|跌破|被动止盈|黄线|止损位|止损线")
unwanted = re.compile(r"[quote]|Post by|Reply")
seen = set()
for sh in ["2026", "小时代狼大楼（最新一周）"]:
    ws = wb[sh]; rows = list(ws.iter_rows(values_only=True))
    print("=====", sh, "=====")
    n = 0
    for dt, txt in rows[1:]:
        if not dt or not txt or not isinstance(dt, str): continue
        if not ("2026-" in dt): continue
        if not kw.search(txt): continue
        if unwanted.search(txt): continue   # 只挑狼大本人说(去转发/问答)
        key = txt[:60]
        if key in seen: continue
        seen.add(key); n += 1
        print("###", dt)
        print(txt[:210])
        print()
        if n >= 12: break
    print("count", n)

# -*- coding: utf-8 -*-
"""fetch_brze_extra_5min.py — 补拉 E09/E10/E11 缺的 8 只代理股 5min（chunk 续拉，仿 fetch_brze_target_5min）
窗口 2025-11-24~2026-07-24（覆盖 E09 02-28/E10 03-19/E11 04-22）
"""
import os, sys, json, time
from datetime import date, timedelta
for _p in ("/app", "/app/core", "/app/app"):
    if _p not in sys.path: sys.path.insert(0, _p)
from app.services.t_data_sources import fetch_brze_stk_mins

CODES = ["002156.SZ", "002185.SZ", "688362.SH", "002851.SZ",
         "688300.SH", "603931.SH", "300655.SZ", "002409.SZ"]
D0 = date(2025, 11, 24); D1 = date(2026, 7, 24)
def chunks():
    d = D0
    while d <= D1:
        e = min(d + timedelta(days=19), D1)
        yield d.strftime('%Y%m%d'), e.strftime('%Y%m%d')
        d = e + timedelta(days=1)
CH = list(chunks())
print('codes', len(CODES), 'chunks', len(CH), flush=True)
for code in CODES:
    out = "/app/data/stock_5m_bt/%s.json" % code[:6]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        data = json.load(open(out, encoding='utf-8'))
    except Exception:
        data = {}
    for s, e in CH:
        got = [k for k in data if s <= k <= e]
        if len(got) >= 5:
            continue
        for attempt in range(3):
            try:
                bars = fetch_brze_stk_mins(code, freq='5min', start_date=s, end_date=e)
            except Exception as ex:
                print(code, s, e, 'ERR', str(ex)[:80], flush=True); time.sleep(2); bars = None
            if bars:
                for b in bars:
                    t = str(b.get('time') or '')[:10].replace('-', '')
                    if t:
                        data.setdefault(t, []).append(b)
                break
            time.sleep(2)
        json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
        time.sleep(0.4)
    for k in data:
        seen = set(); u = []
        for b in sorted(data[k], key=lambda x: str(x.get('time'))):
            key = str(b.get('time')) + str(b.get('open')) + str(b.get('close'))
            if key in seen: continue
            seen.add(key); u.append(b)
        data[k] = u
    json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
    print('DONE', code, 'days', len(data), flush=True)
print('ALL DONE', flush=True)

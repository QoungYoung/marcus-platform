# -*- coding: utf-8 -*-
import os
import requests, json
URL = "https://pcd.mobcvb.cn/tushare/pro"; KEY = (os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
def call(api, params, fields=""):
    body = {"api_name":api,"token":KEY,"params":params,"fields":fields}
    r = requests.post(URL, json=body, timeout=40, verify=False)
    d = r.json(); return (d.get("data") or {}).get("items") or []
for dt in ["20260907","20260903"]:
    items = call("stk_mins", {"ts_code":"588170.SH","freq":"5min","trade_date":dt}, "trade_time,open,close,high,low")
    print(dt, "promax stk_mins 588170.SH bars:", len(items))
    if items: print("   first", items[0])

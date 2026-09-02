# -*- coding: utf-8 -*-
"""stock_confirm_judge.py — 个股级确认链(三层联动之三): 主线概念成分股 confirm_chain → 确认比例
流程: main_line_state 主线主题 → THEME_CONCEPTS 概念名 → stock_concept_map 成分股 → pro.daily → confirm_chain
输出: data/stock_confirm_result.json (每概念: n/confirm/ratio/stocks)
运行: 每周一 position_judge 后置(需 tushare 网络, ~50次 pro.daily 调用 1-2分钟)
"""
import sqlite3, json, os, sys, time
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from confirm_chain import confirm_chain
import urllib3
urllib3.disable_warnings()
import requests

DATA = os.environ.get("DATA_DIR", "/app/data")
DB = os.path.join(DATA, "stock_pool.db")
U = "https://pcd.mobcvb.cn/tushare/pro"
K = "tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE"
MAX_STOCKS = int(os.getenv("STOCK_CONFIRM_MAX", "10"))
MAX_CONCEPTS = int(os.getenv("STOCK_CONFIRM_CONCEPTS", "5"))

def ts(a, **p):
    for _ in range(3):
        try:
            return requests.get(f"{U}/{a}", params=p, headers={"X-API-Key": K}, verify=False, timeout=30).json()
        except Exception:
            time.sleep(1)
    return {}

def main():
    # 主线主题 → 概念名(fusion THEME_CONCEPTS)
    try:
        import fusion_mainline as fm
        st = json.load(open(os.path.join(DATA, "main_line_state.json"), encoding="utf-8"))
        mt = st.get("main_line") or "AI/算力/科技"
        names = fm.THEME_CONCEPTS.get(mt, [])
    except Exception:
        names = ["人工智能", "算力概念", "CPO概念", "光通信模块", "液冷概念"]
    print("[stock_confirm] 主线:", mt if 'mt' in dir() else "AI/算力/科技", "概念候选:", len(names), file=sys.stderr)
    if not os.path.exists(DB):
        print("[stock_confirm] NO stock_pool.db", file=sys.stderr); return
    con = sqlite3.connect(DB)
    out = {}
    for cname in names[:MAX_CONCEPTS]:
        try:
            cur = con.cursor()
            cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?", (cname, MAX_STOCKS))
            codes = [r[0] for r in cur.fetchall()]
        except Exception as e:
            print("[stock_confirm] concept err", cname, str(e)[:60], file=sys.stderr); continue
        stocks = []
        for code in codes:
            d = ts("daily", ts_code=code, start_date="20260601", end_date="20260901")
            data = d.get("data", {}); fields = data.get("fields") or []; items = data.get("items") or []
            if not items: continue
            df = pd.DataFrame([dict(zip(fields, it)) for it in items])
            df["trade_date"] = pd.to_datetime(df["trade_date"]); df = df.sort_values("trade_date")
            ser = df.set_index("trade_date")["close"].astype(float)
            vol = df.set_index("trade_date")["vol"].astype(float)
            cc = confirm_chain(ser, None, vol)
            stocks.append({"code": code, "stage": cc["stage"]})
            time.sleep(0.5)
        n_confirm = sum(1 for s in stocks if s["stage"] in ("确认", "突破候选"))
        out[cname] = {"n": len(stocks), "confirm": n_confirm,
                      "ratio": round(n_confirm / max(len(stocks), 1), 2), "stocks": stocks}
        print("[stock_confirm]", cname, "n=", len(stocks), "确认", n_confirm, file=sys.stderr)
    json.dump(out, open(os.path.join(DATA, "stock_confirm_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[stock_confirm] WROTE stock_confirm_result.json 概念:", list(out.keys()), file=sys.stderr)

if __name__ == "__main__":
    main()

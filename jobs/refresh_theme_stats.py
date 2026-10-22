# -*- coding: utf-8 -*-
"""补齐 theme_strength / theme_breadth 的缺失父主题（原生覆盖）。"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "backend/app/services"),
           os.path.join(ROOT, "app/services"), ROOT):
    if os.path.isdir(_p): sys.path.insert(0, _p)
from wolf_card.subthemes import SUBTEMES, stocks as sub_stocks, parent

DATA = os.path.join(ROOT, "data")
ST = os.path.join(DATA, "theme_strength_daily.parquet")
BR = os.path.join(DATA, "theme_breadth_daily.parquet")


def main():
    st = pd.read_parquet(ST); br = pd.read_parquet(BR)
    parents = {parent(x) for x in SUBTEMES}
    missing = sorted(parents - set(st.theme.unique()))
    print("missing", missing)
    if not missing:
        print("no missing theme"); return
    bars = pd.read_parquet(os.path.join(DATA, "bars_combined.parquet"),
                           columns=["ts_code", "trade_date", "close", "amount"])
    # market total amount by date
    mkt = bars.groupby("trade_date").amount.sum()
    add_st, add_br = [], []
    for theme in missing:
        codes = sorted({c for s in SUBTEMES if parent(s) == theme for c in sub_stocks(s)})
        x = bars[bars.ts_code.isin(codes)]
        piv = x.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
        amt = x.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
        dates = [d for d in st[st.theme == st.theme.iloc[0]].trade_date.astype(str) if d in piv.index]
        for d in dates:
            try:
                s = piv.loc[:d].tail(61); a = amt.loc[:d].tail(60)
                if len(s) < 21: continue
                r5 = float((s.iloc[-1] / s.iloc[-6] - 1).mean())
                r20 = float((s.iloc[-1] / s.iloc[-21] - 1).mean())
                share5 = float(a.tail(5).sum().sum() / mkt.loc[:d].tail(5).sum())
                ma20 = s.rolling(20).mean().iloc[-1]; ma60 = s.rolling(60).mean().iloc[-1]
                last = s.iloc[-1]
                b20 = float((last > ma20).mean()); b60 = float((last > ma60).mean())
                b20_5 = float((s.iloc[-6] > s.rolling(20).mean().iloc[-6]).mean()) if len(s) >= 26 else b20
                ret = s.pct_change().iloc[-1]
                add_st.append({"trade_date": d, "theme": theme, "r5": r5, "r20": r20,
                               "share5": share5, "n_stocks": len(codes)})
                add_br.append({"trade_date": d, "theme": theme, "breadth20": b20, "breadth60": b60,
                               "breadth_chg5": b20 - b20_5,
                               "limit_up": int((ret >= 0.095).sum()),
                               "new_high20": int((last >= s.iloc[:-1].tail(20).max()).sum()),
                               "new_low20": int((last <= s.iloc[:-1].tail(20).min()).sum()),
                               "n_stocks": len(codes)})
            except Exception:
                continue
    if not add_st:
        print("nothing generated"); return
    st = pd.concat([st, pd.DataFrame(add_st)], ignore_index=True).drop_duplicates(["trade_date","theme"], keep="last")
    br = pd.concat([br, pd.DataFrame(add_br)], ignore_index=True).drop_duplicates(["trade_date","theme"], keep="last")
    for df, cols in [(st, ["r5","r20","share5"]), (br, [])]:
        for c in cols:
            df[c + "_rank"] = df.groupby("trade_date")[c].rank(ascending=False, method="min").astype(int)
    st = st.sort_values(["trade_date","theme"]); br = br.sort_values(["trade_date","theme"])
    st.to_parquet(ST, index=False); br.to_parquet(BR, index=False)
    print("added", len(add_st), "strength rows,", len(add_br), "breadth rows")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""每日维护“确认风向标”：锚定确认表，只在接力条件满足时切换。

不是把每日 proxy 直接覆盖 confirmed。
切换条件（全部满足）：
  1. 新候选连续 2 个交易日是 proxy top1；
  2. 不是旧风向标；
  3. 创近 20 日新高；
  4. 5 日相对旧风向标 >= +2pp；
  5. 旧风向标确实走弱（旧 r5 < 子主题 r5 - 1pp 或旧 r5 < 0）；
  6. 子主题广度 >= 0.35。
"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend")); sys.path.insert(0, os.path.join(ROOT, "backend/app/services"))
from wolf_card.subthemes import stocks as sub_stocks

OUT = os.path.join(ROOT, "data/core_p05")
ANCHOR = os.path.join(ROOT, "config/core_p05_confirmed.json")
PROXY = os.path.join(OUT, "subtheme_bellwethers.csv")
DAILY = os.path.join(OUT, "confirmed_daily.json")
CHANGES = os.path.join(OUT, "confirmed_changes.csv")


def _load():
    if os.path.exists(DAILY):
        return json.load(open(DAILY, encoding="utf-8"))
    x = json.load(open(ANCHOR, encoding="utf-8"))
    return {"date": x.get("date"), "source": "anchor", "items": x.get("items", [])}


def _bars():
    b = pd.read_parquet(os.path.join(ROOT, "data/bars_combined.parquet"),
                        columns=["ts_code", "trade_date", "close", "amount"])
    h = pd.read_parquet(os.path.join(ROOT, ".dsh-tmp/buyside/bars.parquet"),
                        columns=["ts_code", "trade_date", "high"])
    b = b.merge(h, on=["ts_code", "trade_date"], how="left")
    close = b.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    high = b.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
    amt = b.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    return close, high, amt


def _r5(close, code, d):
    s = close.loc[:d, code].dropna()
    if len(s) < 6: return None
    return float(s.iloc[-1] / s.iloc[-6] - 1)


def _new_high(close, high, code, d):
    try:
        c = close.at[d, code]
        prev = high.loc[:d, code].dropna().tail(21).iloc[:-1].max()
        return bool(pd.notna(c) and pd.notna(prev) and c >= prev * 0.995)
    except Exception:
        return False


def _breadth(close, sub, d):
    codes = [c for c in sub_stocks(sub) if c in close.columns]
    if not codes: return None
    s = close.loc[:d, codes].tail(20)
    if len(s) < 5: return None
    ma = s.rolling(20).mean().iloc[-1]
    return float((s.iloc[-1] > ma).sum() / len(codes))


def _sub_r5(close, sub, d):
    codes = [c for c in sub_stocks(sub) if c in close.columns]
    if not codes: return None
    s = close.loc[:d, codes].tail(10)
    if len(s) < 6: return None
    return float((s.iloc[-1] / s.iloc[-6] - 1).mean())


def main():
    anchor = _load()
    close, high, amt = _bars()
    p = pd.read_csv(PROXY, dtype={"date": str, "bellwether_code": str})
    if p.empty:
        print("no proxy"); return
    dates = sorted(p.date.unique())[-2:]
    latest = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else None
    rows = []
    changes = []
    for it in anchor.get("items", []):
        sub, old = it["subtheme"], it["code"]
        cur = {**it}
        cur["as_of"] = latest
        cands = []
        if prev:
            a = p[(p.date == prev) & (p.subtheme == sub)]
            b = p[(p.date == latest) & (p.subtheme == sub)]
            if len(a) and len(b):
                ca, cb = str(a.iloc[0].bellwether_code), str(b.iloc[0].bellwether_code)
                if ca == cb and cb != old:
                    cands.append((cb, str(b.iloc[0].bellwether_name or cb)))
        if not cands:
            cur["switch"] = False
            cur["reason"] = "no_2day_candidate"
            rows.append(cur); continue
        c, cname = cands[0]
        cr5 = _r5(close, c, latest); or5 = _r5(close, old, latest)
        sr5 = _sub_r5(close, sub, latest); br = _breadth(close, sub, latest)
        nh = _new_high(close, high, c, latest)
        old_weak = bool(cr5 is not None and or5 is not None and sr5 is not None and (or5 < sr5 - 0.01 or or5 < 0))
        rel_ok = bool(cr5 is not None and or5 is not None and cr5 - or5 >= 0.02)
        breadth_ok = bool(br is not None and br >= 0.35)
        ok = bool(nh and rel_ok and old_weak and breadth_ok)
        if ok:
            old_name = cur.get("name")
            cur.update({"code": c, "name": cname, "switch": True, "previous": old,
                        "reason": f"relay: new_high={nh}, rel={cr5-or5:.2%}, old_weak={old_weak}, breadth={br:.2f}"})
            changes.append({"date": latest, "subtheme": sub, "old_code": old, "old_name": old_name,
                            "new_code": c, "new_name": cname, "reason": cur["reason"]})
        else:
            cur["switch"] = False
            cur["reason"] = f"keep: new_high={nh}, rel_ok={rel_ok}, old_weak={old_weak}, breadth_ok={breadth_ok}"
        rows.append(cur)
    out = {"date": latest, "source": "anchor+relay", "items": rows}
    json.dump(out, open(DAILY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if changes:
        df = pd.DataFrame(changes)
        if os.path.exists(CHANGES):
            old = pd.read_csv(CHANGES)
            df = pd.concat([old, df], ignore_index=True)
        df.to_csv(CHANGES, index=False, encoding="utf-8-sig")
    print("date", latest, "changes", len(changes))
    for ch in changes: print(ch)


if __name__ == "__main__":
    main()

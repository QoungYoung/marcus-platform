# -*- coding: utf-8 -*-
"""最近 4 个 PIT 周度日期的 core 动作闸历史回放。"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from backtest_core_pit import stage_of, build_states, state_at, LAB, OBS

OUT = os.path.join(ROOT, "data/core_pit")
SUFFIX = os.environ.get("REPLAY_SUFFIX", "")
bars = pd.read_parquet(os.path.join(ROOT, "data/bars_api.parquet"),
                       columns=["ts_code", "trade_date", "close", "high", "amount"])
close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
high = bars.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
lab = pd.read_csv(LAB, dtype={"date": str, "ts_code": str})
obs = pd.read_csv(OBS, dtype={"as_of_date": str, "ts_code": str})
build_states(lab, obs, close, high, amt)
idx = {str(d): i for i, d in enumerate(close.index)}
_env_dates = os.environ.get("REPLAY_DATES", "")
dates = _env_dates.split(",") if _env_dates else sorted(lab.date.unique())[-4:]
rows = []
for d in dates:
    if d not in idx: continue
    i = idx[d]; day = lab[lab.date == d]
    for theme, g in day.groupby("theme"):
        m = obs[(obs.as_of_date == d) & (obs.theme == theme)].ts_code.dropna().unique().tolist()
        if not m: m = g.ts_code.dropna().unique().tolist()
        st, _ = stage_of(m, close, d)
        if st in ("low_flat", "oversold", "unknown"): continue
        cand = g[g.bucket == "second"] if st in ("strong", "extended") else g[g["rank"] == 1]
        if not len(cand): cand = g[g["rank"] == 1]
        cand = cand.drop_duplicates("ts_code").head(2)
        ls = state_at(theme, d)
        for _, p in cand.iterrows():
            role = "second" if p.get("bucket") == "second" else "leader"
            allow = not (ls == "dead" or (ls in ("warning", "stale", "unknown") and role == "second"))
            c = str(p.ts_code)
            if c not in close.columns: continue
            rec = {"date": d, "theme": theme, "code": c, "name": p.get("name"), "stage": st,
                   "role": role, "bellwether_state": ls, "allow": bool(allow),
                   "block_reason": "" if allow else ("dead" if ls == "dead" else f"{ls}:second")}
            for hz in (5, 10, 20):
                j = i + 1 + hz
                if j >= len(close.index): continue
                e = close[c].iloc[i + 1]; f = close[c].iloc[j]
                if pd.notna(e) and pd.notna(f) and e > 0: rec[f"f{hz}"] = round(float(f / e - 1) * 100, 2)
            rows.append(rec)
df = pd.DataFrame(rows)
if df.empty:
    summary = {"dates": dates, "n": 0, "note": "no picks in this window"}
    json.dump(summary, open(os.path.join(OUT, f"replay_4w{SUFFIX}.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False)); raise SystemExit(0)
df.to_csv(os.path.join(OUT, f"replay_4w{SUFFIX}.csv"), index=False, encoding="utf-8-sig")
summary = {"dates": dates, "n": len(df), "allowed": int(df.allow.sum()), "blocked": int((~df.allow).sum()),
           "by_state": df.groupby(["bellwether_state", "allow"]).size().rename("n").reset_index().to_dict("records"),
           "by_stage": df.groupby(["stage", "allow"]).size().rename("n").reset_index().to_dict("records")}
for hz in (5, 10, 20):
    col = f"f{hz}"
    if col in df:
        a = df[df.allow][col].dropna(); b = df[~df.allow][col].dropna()
        summary[col] = {"allowed": {"n": len(a), "mean": round(float(a.mean()), 2) if len(a) else None},
                        "blocked": {"n": len(b), "mean": round(float(b.mean()), 2) if len(b) else None}}
json.dump(summary, open(os.path.join(OUT, f"replay_4w{SUFFIX}.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))

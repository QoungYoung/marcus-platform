# -*- coding: utf-8 -*-
"""风向标事件 v1：cycle_start + last_high 双锚点，leader 换人重置，dead 按段计数。"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "data/pit/chain_map/labels_weekly.csv")
OBS = os.path.join(ROOT, "data/pit/theme_members/observations.csv")
BARS = os.path.join(ROOT, "data/bars_api.parquet")
OUT = os.path.join(ROOT, "data/core_pit")
os.makedirs(OUT, exist_ok=True)


def impulses(close, high, amt, low, code, d, lookback=120):
    """返回 lookback 内的放量新高序列：[(date, high, low, close)]。"""
    if code not in close.columns: return []
    s = close[code].loc[:d].tail(lookback).dropna()
    if len(s) < 25: return []
    hi = high[code].reindex(s.index); am = amt[code].reindex(s.index)
    ma20 = am.rolling(20).mean()
    out = []; prev = None
    for dt in s.index:
        h = hi.get(dt); a = am.get(dt)
        if pd.isna(h) or pd.isna(a): continue
        vol_ok = pd.notna(ma20.get(dt)) and a >= ma20.get(dt) * 1.2
        if prev is None:
            new_high = h >= hi.loc[:dt].tail(21).max() * 0.999
        else:
            new_high = h > prev * 1.001
        if new_high and vol_ok:
            lv = low[code].loc[dt] if code in low.columns and pd.notna(low[code].loc[dt]) else s.loc[dt]
            out.append((str(dt), float(h), float(lv), float(s.loc[dt])))
            prev = float(h)
    return out


def main():
    lab = pd.read_csv(LAB, dtype={"date": str, "ts_code": str})
    obs = pd.read_csv(OBS, dtype={"as_of_date": str, "ts_code": str})
    bars = pd.read_parquet(BARS, columns=["ts_code","trade_date","close","high","low","amount"])
    close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    high = bars.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
    low = bars.pivot_table(index="trade_date", columns="ts_code", values="low", aggfunc="last").sort_index()
    amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    rows = []
    for (theme, seg), gg in lab.groupby(["theme", "segment_role"]):
        gg = gg.sort_values("date")
        prev_leader = None; prev_state = None; cycle = None; episode = 0
        for d in sorted(gg.date.unique()):
            if d not in close.index: continue
            g = gg[gg.date == d]
            lead = g[g["rank"] == 1].drop_duplicates("ts_code")
            if not len(lead): continue
            code = str(lead.iloc[0].ts_code)
            rotated = False; change = False
            if prev_leader and code != prev_leader:
                rotated = prev_state == "dead"
                cycle = None; change = True
            prev_leader = code
            imp = impulses(close, high, amt, low, code, d)
            if cycle is None and imp:
                f = imp[0]
                cycle = {"start": f[0], "start_close": f[3], "last_high": f[1],
                         "last_low": f[2], "last_impulse": f[0]}
            elif cycle is not None and imp:
                later = [x for x in imp if x[0] > cycle["start"]]
                if later:
                    z = later[-1]
                    cycle["last_high"] = max(cycle["last_high"], z[1])
                    cycle["last_low"] = z[2]
                    cycle["last_impulse"] = z[0]
            c = close[code].loc[:d].iloc[-1]
            state = "unknown"; reason = ""
            leader_ret = None; dead_line = None
            if cycle and pd.notna(cycle["start_close"]) and cycle["start_close"] > 0:
                leader_ret = float(c / cycle["start_close"] - 1)
                # low 缺失时用 cycle 起点收盘的 10% 失效线
                dead_line = cycle["last_low"] * 0.98 if cycle["last_low"] < cycle["last_high"] * 0.98 else cycle["start_close"] * 0.90
                hi_now = high[code].loc[:d].iloc[-1] if code in high.columns else np.nan
                a5 = amt[code].loc[:d].tail(5).mean(); a20 = amt[code].loc[:d].tail(20).mean()
                vol_dist = bool(pd.notna(a20) and a20 > 0 and a5 >= a20 * 1.2 and c < close[code].loc[:d].iloc[-2])
                if c < dead_line:
                    state = "dead"; reason = "跌破cycle失效线"
                elif vol_dist and pd.notna(hi_now) and hi_now < cycle["last_high"] * 0.995:
                    state = "dead"; reason = "放量出货+高点递减"
                elif c >= cycle["last_high"] * 0.98:
                    state = "alive"
                else:
                    state = "warning"; reason = "低于last_high"
            if rotated:
                state = "rotated" if imp else "unknown"; reason = "leader更换"
            change = change or (prev_state is not None and prev_state != state)
            if state == "dead" and prev_state != "dead":
                episode += 1
            # 二线滞后：从 cycle_start 算
            sec = g[g.bucket == "second"].drop_duplicates("ts_code")
            sec_codes = []; lag = None
            if cycle and pd.notna(cycle["start_close"]) and cycle["start_close"] > 0:
                for _, s in sec.iterrows():
                    sc = str(s.ts_code)
                    if sc in close.columns and pd.notna(close[sc].loc[:d].iloc[-1]):
                        cs = close[sc].loc[:d].iloc[-1]
                        if pd.notna(cs) and leader_ret is not None:
                            lv = float(leader_ret - (cs / cycle["start_close"] - 1))
                            lag = lv if lag is None else max(lag, lv)
                            if lv >= 0.05: sec_codes.append(sc)
            ext = bool(leader_ret is not None and (leader_ret >= 0.12 or (code in close.columns and c >= close[code].loc[:d].tail(20).mean() * 1.15)))
            catchup = bool(state == "alive" and leader_ret is not None and leader_ret >= 0.08 and sec_codes)
            hcl = bool(state == "alive" and ext and sec_codes)
            rows.append({"date": d, "theme": theme, "segment_role": seg, "leader": code,
                         "state": state, "state_change": change, "rotated": rotated,
                         "reason": reason, "cycle_start": (cycle or {}).get("start"),
                         "last_impulse": (cycle or {}).get("last_impulse"),
                         "last_high": (cycle or {}).get("last_high"),
                         "dead_line": None if dead_line is None else round(float(dead_line), 4),
                         "leader_ret": None if leader_ret is None else round(leader_ret, 4),
                         "lag": None if lag is None else round(float(lag), 4),
                         "extended": ext, "second_codes": ",".join(sec_codes),
                         "second_catchup": catchup, "high_cut_low": hcl,
                         "dead_episode": episode if state == "dead" else None})
            prev_state = state
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "bellwether_events.csv"), index=False, encoding="utf-8-sig")
    # 段级统计：dead 只数 episode，不数周
    episodes = int(df.dead_episode.nunique(dropna=True))
    transitions = df[df.state_change].groupby(["date","theme","segment_role"]).size().shape[0]
    summary = {"n_rows": len(df), "dates": int(df.date.nunique()), "themes": int(df.theme.nunique()),
               "state": df.state.value_counts().to_dict(),
               "dead_episodes": episodes, "state_changes": int(transitions),
               "second_catchup": int(df.second_catchup.sum()),
               "high_cut_low": int(df.high_cut_low.sum()), "rotated": int(df.rotated.sum()),
               "dead_rows": int((df.state == "dead").sum())}
    json.dump(summary, open(os.path.join(OUT, "bellwether_events_summary.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()

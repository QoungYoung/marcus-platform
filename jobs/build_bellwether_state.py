# -*- coding: utf-8 -*-
"""P1：全部子主题风向标 v3（高点递减 + 放量 + 接力/广度）。

语料依据：
  · 2021-08-09「一次比一次低，放量的情况就撤」
  · 2021-06-21「还在顶/死不掉」「拉了一波，力竭了」
  · 2025-12-06「风向标被摁跌停或更换了，只要换得快、接得上，只影响短期」
  · 2021-04-13「风向标也可能没有被合力而反方向向下」
状态：
  alive / weakening / dead / rotated / unknown
"""
from __future__ import annotations
import argparse, json, os, sys
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "backend/app/services"),
           os.path.join(ROOT, "app/services"), ROOT):
    if os.path.isdir(_p): sys.path.insert(0, _p)
from wolf_card.subthemes import stocks as sub_stocks, parent
from bars_source import load_bars
from app.services.wolf_card.bellwether_context import permissions_for_state

OUT = os.path.join(ROOT, "data/core_p1"); os.makedirs(OUT, exist_ok=True)
REGISTRY = os.path.join(ROOT, "data/core_p05/confirmed_daily.json")
FALLBACK = os.path.join(ROOT, "config/core_p05_confirmed.json")
_CHAIN_CACHE = {}


def _chain_roles(date8, theme):
    """返回该主题当日/最近可用的 chain_map 角色；只认 leader/second。"""
    key = (date8, theme)
    if key in _CHAIN_CACHE:
        return _CHAIN_CACHE[key]
    import glob, json
    files = sorted([p for p in glob.glob(os.path.join(ROOT, "data/chain_map_2026*.json"))
                    if "_v2_cmp" not in p])
    use = None
    for p in files:
        d = os.path.basename(p).replace("chain_map_", "").replace(".json", "")
        if d <= date8:
            use = p
    if not use:
        _CHAIN_CACHE[key] = None
        return None
    try:
        x = json.load(open(use, encoding="utf-8"))
    except Exception:
        _CHAIN_CACHE[key] = None
        return None
    roles = {}
    for t in x.get("themes", []):
        if t.get("theme") != theme: continue
        for seg in t.get("segments", []):
            for i, z in enumerate(seg.get("leading_verified") or [], 1):
                roles[z.get("ts")] = {"role": "leader", "rank": i, "segment": seg.get("label")}
            lead = {z.get("ts") for z in seg.get("leading_verified") or []}
            for z in seg.get("verified_all") or []:
                if z.get("ts") in lead: continue
                roles[z.get("ts")] = {"role": "second", "rank": None, "segment": seg.get("label")}
    _CHAIN_CACHE[key] = roles or None
    return _CHAIN_CACHE[key]


def load_confirmed():
    """全部 45 个子主题：优先当日 agent 确认，其次确认锚。"""
    path = REGISTRY if os.path.exists(REGISTRY) else FALLBACK
    x = json.load(open(path, encoding="utf-8"))
    return x.get("items", [])


def _breadth(df, codes, date8):
    s = df.loc[:date8, codes].tail(20)
    if len(s) < 5: return np.nan
    ma = s.rolling(20).mean().iloc[-1]
    last = s.iloc[-1]
    ok = (last > ma).sum()
    return float(ok / max(len(codes), 1))


def compute_history(df, high, amt, sub, bell, min_date="20260615"):
    codes = [c for c in sub_stocks(sub) if c in df.columns]
    if not codes or bell not in df.columns:
        return []
    subp = df[codes]
    sret = subp.pct_change().mean(axis=1)
    sr5 = sret.rolling(5).sum()
    imp = (sret > 0) & ((sr5.shift(1) < 0) | (sret >= 0.015))
    imp = imp & (sret.index >= min_date)
    wb_ret = df[bell].pct_change()
    rows = []
    last = None
    state = "unknown"
    days_since = 0
    dead_streak = 0
    for d in sret.index:
        if not imp.loc[d]:
            # 无 impulse：最多 carry 3 日；之后按“是否创出上次 impulse 高点”刷新，
            # 不再让 warning/dead 无限延续（2026-01-28 北方华创案例）。
            if last is None:
                state = "unknown"
            else:
                days_since += 1
                if days_since <= 3:
                    state = last.get("state", state)
                else:
                    close_now = df.at[d, bell] if d in df.index and bell in df.columns else np.nan
                    amt_now = amt.at[d, bell] if d in amt.index and bell in amt.columns else np.nan
                    amt20 = amt.loc[:d, bell].tail(20).mean() if bell in amt.columns else np.nan
                    vol_now = amt_now / amt20 if pd.notna(amt_now) and pd.notna(amt20) and amt20 else np.nan
                    lh = last.get("high")
                    if pd.notna(close_now) and pd.notna(lh) and close_now >= lh and not (pd.notna(vol_now) and vol_now > 1.2 and close_now < df.at[d, bell] * 0.995):
                        state = "alive"
                    elif pd.notna(close_now) and pd.notna(lh) and close_now < lh * 0.95:
                        state = "warning"
                    else:
                        state = "stale"
            rows.append({"date": str(d), "subtheme": sub, "state": state, "impulse": False,
                         "bellwether_code": bell, "high": None, "prev_impulse_high": None,
                         "lower_high": None, "vol_dist": None, "breadth": None,
                         "breadth_prev": None, "successor": None, "successor_code": None,
                         "bell_ret": None, "sub_ret": None})
            continue
        hi = float(high.at[d, bell]) if d in high.index and bell in high.columns and pd.notna(high.at[d, bell]) else np.nan
        br = wb_ret.loc[d] if pd.notna(wb_ret.loc[d]) else np.nan
        sr = float(sret.loc[d])
        breadth = _breadth(df, codes, str(d))
        amt_now = amt.at[d, bell] if d in amt.index and bell in amt.columns else np.nan
        amt20 = amt.loc[:d, bell].tail(20).mean() if bell in amt.columns else np.nan
        vol_ratio = amt_now / amt20 if pd.notna(amt_now) and pd.notna(amt20) and amt20 else np.nan
        prev_high = last["high"] if last else None
        prev_breadth = last["breadth"] if last else None
        lower_high = bool(pd.notna(prev_high) and pd.notna(hi) and hi <= prev_high * 0.995) if prev_high else None
        vol_dist = bool(pd.notna(vol_ratio) and vol_ratio > 1.2 and (pd.isna(br) or br < sr))
        # 接力：非风向标里，5日相对最强且明显跑赢
        # 狼大口径：同链 leader/second + 相对强 + 创新高 + 换得快。
        # 不再用“成交额前5的任意上涨票”。
        roles = _chain_roles(str(d), parent(sub))
        best = None; best_r5 = -9
        try:
            top_codes = amt.loc[:d, [c for c in codes if c in amt.columns]].tail(20).mean().nlargest(10).index.tolist()
        except Exception:
            top_codes = []
        for c in top_codes:
            if c == bell: continue
            if roles is not None and c not in roles: continue
            s = df[c].loc[:d].dropna().tail(6)
            if len(s) < 6: continue
            r5 = float(s.iloc[-1] / s.iloc[0] - 1)
            # 真接力需要创出近20日新高
            try:
                prior_high = high.loc[:d, c].dropna().tail(21).iloc[:-1].max()
            except Exception:
                prior_high = np.nan
            if pd.isna(prior_high) or s.iloc[-1] < prior_high * 0.995: continue
            if r5 > best_r5:
                best_r5, best = r5, c
        successor = bool(best and pd.notna(br) and best_r5 > 0 and best_r5 - (br if pd.notna(br) else 0) > 0.02)
        follow = bool(pd.notna(breadth) and breadth >= 0.5 and (prev_breadth is None or breadth >= prev_breadth - 0.05))
        dead_candidate = bool(lower_high and vol_dist and not successor)
        dead_streak = dead_streak + 1 if dead_candidate else 0
        if lower_high and successor and pd.notna(breadth) and breadth >= 0.4:
            state = "rotated"
        elif dead_candidate and dead_streak >= 2:
            state = "dead"
        elif dead_candidate:
            state = "warning"
        elif lower_high is False and follow and not vol_dist:
            state = "alive"
        else:
            state = "warning"
        rows.append({"date": str(d), "subtheme": sub, "state": state, "impulse": True,
                     "bellwether_code": bell, "high": round(hi, 3) if pd.notna(hi) else None,
                     "prev_impulse_high": round(prev_high, 3) if prev_high else None,
                     "lower_high": lower_high, "vol_dist": vol_dist,
                     "breadth": round(breadth, 3) if pd.notna(breadth) else None,
                     "breadth_prev": round(prev_breadth, 3) if prev_breadth is not None else None,
                     "successor": successor, "successor_code": best,
                     "bell_ret": round(float(br) * 100, 2) if pd.notna(br) else None,
                     "sub_ret": round(sr * 100, 2)})
        days_since = 0
        last = {"high": hi, "breadth": breadth, "state": state}
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()
    bars = load_bars(min_date="20260601")
    df = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    high = bars.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
    amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    allrows = []
    for i in load_confirmed():
        allrows += compute_history(df, high, amt, i["subtheme"], i["code"])
    out = pd.DataFrame(allrows)
    if a.date:
        out = out[out.date == a.date]
    latest_date = str(out.date.max()) if len(out) else (a.date or str(df.index[-1]))
    if a.history:
        out.to_csv(os.path.join(OUT, "bellwether_history.csv"), index=False, encoding="utf-8-sig")
    latest = out[out.date == latest_date] if len(out) else out
    items = []
    for sub in sorted(out.subtheme.unique()) if len(out) else []:
        s = out[out.subtheme == sub].sort_values("date")
        if not len(s): continue
        r = s.iloc[-1].to_dict()
        r["alive"] = bool(r.get("state") == "alive")
        r["permissions"] = permissions_for_state(r.get("state"), r.get("successor_code"))
        items.append(r)
    json.dump({"date": latest_date, "source": "all_subthemes_confirmed_highs_volume_successor_v3",
               "usage": "environment_gate_not_alpha", "items": items},
              open(os.path.join(OUT, "bellwether_state_latest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("date", latest_date, "rows", len(out), "states", latest.state.value_counts().to_dict() if len(latest) else {})
    if items:
        cols = ["subtheme", "state", "lower_high", "vol_dist", "breadth", "successor", "successor_code"]
        print(pd.DataFrame(items)[cols].to_string(index=False))


if __name__ == "__main__":
    main()

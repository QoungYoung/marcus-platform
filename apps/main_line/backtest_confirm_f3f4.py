# -*- coding: utf-8 -*-
"""F3/F4 验证: 概念级确认链 加 mainline_act(主线响应) 后, F3证伪组是否被正确拦下
主线历史代理: 当日资金流入最大主题(9主题) → 其概念近5日响应度 = mainline_act
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import position_class as pc
from confirm_chain import confirm_chain, mainline_act
import fusion_mainline as fm

DATA = os.environ.get("DATA_DIR", "data")
hist = json.load(open(os.path.join(DATA, "concept_hist.json"), encoding="utf-8"))

def close_series(a):
    idx = pd.to_datetime(a["dates"])
    return pd.Series(a["close"], index=idx).apply(pd.to_numeric, errors="coerce").dropna().astype(float)
def net_series(a):
    idx = pd.to_datetime(a["dates"])
    s = pd.Series(a["net_amount"], index=idx).apply(pd.to_numeric, errors="coerce")
    return s.apply(lambda x: None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x))
def fwd(ser, upto, n):
    base = ser[ser.index <= pd.Timestamp(upto)]; fut = ser[ser.index > pd.Timestamp(upto)]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    return (float(fut.iloc[n-1]) / b0 - 1) * 100 if b0 > 0 else None

def main_theme_at(hist, dt):
    """当日资金流入最大主题(主线代理)"""
    t = pd.Timestamp(dt)
    theme_net = {}
    for th, names in fm.THEME_CONCEPTS.items():
        tot = 0.0
        for code, a in hist.items():
            if a["name"] not in names: continue
            s = net_series(a)[lambda x: x.index <= t].dropna()
            if len(s): tot += float(s.iloc[-1])
        theme_net[th] = tot
    return max(theme_net, key=theme_net.get) if theme_net else None

def collect_low_samples():
    cal = sorted({d for a in hist.values() for d in a["dates"]})
    dates = []
    for i, d in enumerate(cal):
        if d < "20251124": continue
        if len(cal) - i <= 25: break
        if not dates or i - cal.index(dates[-1]) >= 20:
            dates.append(d)
    samples = []
    for dt in dates:
        t = pd.Timestamp(dt)
        for code, a in hist.items():
            ser = close_series(a); base = ser[ser.index <= t]
            if len(base) < 60 or float(base.min()) <= 0: continue
            f = pc.position_features(base)
            if f is None: continue
            if pc.classify(f)["position"] != "LOW": continue
            samples.append({"date": dt, "code": code, "name": a["name"]})
    return samples, dates

def main():
    samples, dates = collect_low_samples()
    print(f"LOW 样本: {len(samples)} ({len(dates)} 时点)")
    grp_confirm = []; grp_f3block = []; grp_none = []
    n_f3 = 0
    for s in samples:
        a = hist[s["code"]]; ser = close_series(a); net = net_series(a)
        idx = ser.index; t = pd.Timestamp(s["date"]); i0 = idx.searchsorted(t)
        mt = main_theme_at(hist, s["date"])   # 当日主线(资金TOP)
        ml_act = mainline_act(hist, s["date"], {"main_line": mt}) if mt else None
        found = None; f3 = False
        for k in range(min(30, len(idx) - i0)):
            upto = idx[i0 + k]
            cc = confirm_chain(ser[ser.index <= upto], net[net.index <= upto], None, {}, ml_act)
            if cc["stage"] in ("确认", "突破候选"):
                found = upto; break
            if cc.get("signals", {}).get("F3"):
                f3 = True; found = upto; break   # F3 证伪(主线未响应)
        r = fwd(ser, found, 20) if found else fwd(ser, s["date"], 20)
        if r is None: continue
        if f3: grp_f3block.append(r); n_f3 += 1
        elif found: grp_confirm.append(r)
        else: grp_none.append(r)
    print()
    def show(name, v):
        if v: print(f"  {name}: n={len(v)} mean={np.mean(v):.2f}% hit={np.mean(np.array(v)>0):.2f}")
        else: print(f"  {name}: n=0")
    print("== 有 F3(主线响应) 的确认链 ==")
    show("确认组(主线响应通过)", grp_confirm)
    show("F3证伪组(主线未响应被拦)", grp_f3block)
    show("未确认组", grp_none)
    print(f"  F3 触发: {n_f3} 个 (主线未响应的突破被拦)")
    json.dump({"grp_confirm": grp_confirm, "grp_f3block": grp_f3block, "grp_none": grp_none},
              open(os.path.join(DATA, "confirm_f3f4_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE data/confirm_f3f4_backtest.json")

if __name__ == "__main__":
    main()

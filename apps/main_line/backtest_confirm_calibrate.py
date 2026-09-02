# -*- coding: utf-8 -*-
"""③阈值校准: 概念LOW扩样本(全窗口多时点) + confirm_chain 参数网格搜索
验证: 确认链(触发) vs 未确认 前向20日收益差; 网格搜索 stand_days/hold_back/new_high_w
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import position_class as pc
from confirm_chain import confirm_chain

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

def collect_low_samples():
    """全窗口 LOW 概念样本: 2025-11-24 起每20交易日, 每时点所有 LOW 概念"""
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

def evaluate(params, samples):
    """在样本上评估: 确认组 vs 未确认组 20日前向"""
    grpA = []; grpB = []
    for s in samples:
        a = hist[s["code"]]; ser = close_series(a); net = net_series(a)
        idx = ser.index; t = pd.Timestamp(s["date"]); i0 = idx.searchsorted(t)
        found = None
        for k in range(min(30, len(idx) - i0)):
            upto = idx[i0 + k]
            cc = confirm_chain(ser[ser.index <= upto], net[net.index <= upto], None, params)
            if cc["stage"] in ("确认", "突破候选"):
                found = upto; break
        r = fwd(ser, found, 20) if found else fwd(ser, s["date"], 20)
        if r is None: continue
        (grpA if found else grpB).append(r)
    return grpA, grpB

def main():
    samples, dates = collect_low_samples()
    print(f"扩样本: {len(dates)} 时点, {len(samples)} 个 LOW 概念样本")
    # 基线(默认参数)
    for name, p in [("默认", {}), ("站稳5日", {"stand_days": 5}), ("hold99", {"hold_back": 0.99}),
                    ("新高15", {"new_high_w": 15}), ("新高30", {"new_high_w": 30})]:
        A, B = evaluate(p, samples)
        fa = f"确认 n={len(A)} mean={np.mean(A):.2f}% hit={np.mean(np.array(A)>0):.2f}" if A else "n=0"
        fb = f"未确认 n={len(B)} mean={np.mean(B):.2f}% hit={np.mean(np.array(B)>0):.2f}" if B else "n=0"
        print(f"  [{name}] {fa} | {fb}")
    # 网格搜索
    print()
    print("== 网格搜索 (stand_days×hold_back×new_high_w) ==")
    best = None
    for sd in (2, 3, 5):
        for hb in (0.95, 0.97, 0.99):
            for w in (15, 20, 30):
                p = {"stand_days": sd, "hold_back": hb, "new_high_w": w}
                A, B = evaluate(p, samples)
                if len(A) < 5: continue
                hitA = np.mean(np.array(A) > 0); meanA = np.mean(A)
                # 目标: 确认组命中率高 且 样本够
                score = hitA + 0.3 * min(meanA / 5.0, 1.0) - 0.2 * (len(A) / len(samples))
                if best is None or score > best[0]:
                    best = (score, p, hitA, meanA, len(A), len(B))
    if best:
        _, p, hitA, meanA, nA, nB = best
        print(f"最优参数: {p}")
        print(f"  确认组 n={nA} hit={hitA:.2f} mean={meanA:.2f}% | 未确认 n={nB}")
        json.dump({"best": {"params": p, "hitA": hitA, "meanA": meanA, "nA": nA, "nB": nB},
                   "samples": samples, "dates": dates},
                  open(os.path.join(DATA, "confirm_calibrate.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()

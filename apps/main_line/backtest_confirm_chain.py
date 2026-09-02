# -*- coding: utf-8 -*-
"""确定性门槛回测: LOW 埋伏候选 vs 确认链触发 的前向收益对比
验证: 确认链(缩量止跌→结构→放量突破→站稳)是否提升 LOW 埋伏质量
(狼大: LOW+资金+逻辑=候选, 确认链触发=入场; 2026-06 提前抄底失败=缺确认)
用法: cd 仓库根 && .venv/bin/python apps/main_line/backtest_confirm_chain.py
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import position_class as pc
from confirm_chain import confirm_chain

DATA = os.environ.get("DATA_DIR", "data")

def load(p):
    return json.load(open(os.path.join(DATA, p), encoding="utf-8"))

def close_series(a):
    idx = pd.to_datetime(a["dates"])
    return pd.Series(a["close"], index=idx).apply(pd.to_numeric, errors="coerce").dropna().astype(float)

def net_series(a):
    idx = pd.to_datetime(a["dates"])
    s = pd.Series(a["net_amount"], index=idx).apply(pd.to_numeric, errors="coerce")
    return s.apply(lambda x: None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x))

def fwd(ser, upto, n):
    base = ser[ser.index <= pd.Timestamp(upto)]
    fut = ser[ser.index > pd.Timestamp(upto)]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    if b0 <= 0: return None
    return (float(fut.iloc[n-1]) / b0 - 1) * 100

def main():
    hist = load("concept_hist.json")
    dates = ["20260603", "20260625", "20260716"]  # LOW 集中出现的时点
    grpA = {"20": [], "60": []}; grpB = {"20": [], "60": []}
    details = []
    for dt in dates:
        t = pd.Timestamp(dt)
        for code, a in hist.items():
            ser = close_series(a); base = ser[ser.index <= t]
            if len(base) < 60 or float(base.min()) <= 0: continue
            f = pc.position_features(base)
            if f is None: continue
            if pc.classify(f)["position"] != "LOW": continue
            net = net_series(a)
            # 从 LOW 日向前扫描最多 30 交易日, 找确认链触发
            idx = ser.index
            i0 = idx.searchsorted(t)
            found = None
            for k in range(min(30, len(idx) - i0)):
                upto = idx[i0 + k]
                st = confirm_chain(ser[ser.index <= upto], net[net.index <= upto])
                if st["stage"] in ("确认", "突破候选"):
                    found = (upto, st["stage"], k)
                    break
            # 前向收益
            for n in (20, 60):
                if found:
                    r = fwd(ser, found[0], n)
                    if r is not None: grpA[str(n)].append(r)
                else:
                    r = fwd(ser, dt, n)
                    if r is not None: grpB[str(n)].append(r)
            details.append({"date": dt, "name": a["name"], "confirmed": bool(found),
                            "found_stage": found[1] if found else None,
                            "days_to_confirm": found[2] if found else None,
                            "r20_from_low": fwd(ser, dt, 20), "r60_from_low": fwd(ser, dt, 60)})
    print(f"LOW 样本: {len(details)} | 确认链触发: {sum(1 for d in details if d['confirmed'])} ({100*sum(1 for d in details if d['confirmed'])/len(details):.0f}%)")
    print()
    print("== 确认链触发组(A: 从确认日算) vs 未触发组(B: 从LOW日算, 模拟硬抄底) ==")
    for n in (20, 60):
        a = grpA[str(n)]; b = grpB[str(n)]
        fa = f"n={len(a)} mean={np.mean(a):.2f}% hit={np.mean(np.array(a)>0):.2f}" if a else "n=0"
        fb = f"n={len(b)} mean={np.mean(b):.2f}% hit={np.mean(np.array(b)>0):.2f}" if b else "n=0"
        print(f"  {n}日: 确认组(A) {fa} | 硬抄底组(B) {fb}")
    print()
    print("== 未触发组的失手案例(提前抄底失败) ==")
    fails = [d for d in details if not d["confirmed"] and d.get("r20_from_low") is not None and d["r20_from_low"] < -3]
    for d in sorted(fails, key=lambda x: x["r20_from_low"])[:8]:
        print(f"  {d['date']} {d['name']}: 20日={d['r20_from_low']:.1f}% (未确认仍跌)")
    json.dump({"grpA": grpA, "grpB": grpB, "details": details},
              open(os.path.join(DATA, "confirm_chain_backtest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("WROTE data/confirm_chain_backtest.json")

if __name__ == "__main__":
    main()

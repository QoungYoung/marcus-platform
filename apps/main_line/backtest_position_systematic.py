# -*- coding: utf-8 -*-
"""
backtest_position_systematic.py — position_class 系统性回测（干净版，替代 _bt_sys.py）
====================================================================================
目的：多时点 + 20/60 日 前向收益，验证「单位置 / 位置+资金 / 硬共振(现版)」的区分度，
     并做 macro(大级别) 敏感性 + 量能(vol) 子样本分析。

数据：data/concept_hist.json(521概念×250日 close/net_amount) + data/concept_vol.json(可选,40概念量能代理)
用法：cd 仓库根 && .venv/bin/python apps/main_line/backtest_position_systematic.py
输出：data/position_systematic_backtest.json（明细+聚合）+ stdout 摘要
"""
import os, json, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import position_class as pc

DATA = os.environ.get("DATA_DIR", "data")  # 仓库根下的 data/

def load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p): return {}
    return json.load(open(p, encoding="utf-8"))

# ---------- 基础工具 ----------
def close_series(a):
    idx = pd.to_datetime(a["dates"])
    s = pd.Series(a["close"], index=idx).dropna()
    return s.astype(float)

def net_series(a):
    idx = pd.to_datetime(a["dates"])
    s = pd.Series(a["net_amount"], index=idx)
    return s.apply(lambda x: None if x is None or (isinstance(x,float) and np.isnan(x)) else float(x))

def fund_at(net, upto):
    """dt 当日(含)的资金方向：最后一日符号 + 连续同号天数"""
    s = net[net.index <= pd.Timestamp(upto)].dropna()
    if not len(s): return {"dir":"flat","conv":0,"strength":0}
    last = float(s.iloc[-1])
    conv = 0
    for x in reversed(s.values):
        if (x > 0) == (last > 0) and x != 0: conv += 1
        else: break
    return {"dir":"in" if last>0 else ("out" if last<0 else "flat"), "conv":conv, "strength":round(last/1e8,1)}

def fwd_ret(ser, upto, n):
    """从 upto(含)起算未来 n 个交易日收益 %；不足或含空返回 None"""
    base = ser[ser.index <= pd.Timestamp(upto)]
    fut = ser[ser.index > pd.Timestamp(upto)]
    if len(base) < 1 or len(fut) < n: return None
    if base.isna().any() or fut.isna().any(): return None
    b0 = float(base.iloc[-1]); f1 = float(fut.iloc[n-1])
    if b0 <= 0: return None
    return (f1/b0 - 1) * 100

def vol_feat_at(vol_series, upto, need=120):
    """upto 时的量能特征：vol_pct120/vol_z20/ratio（不足 need 天返回 None, 并给 n）"""
    s = vol_series[vol_series.index <= pd.Timestamp(upto)]
    if len(s) < 30: return None
    v = s.values.astype(float)
    n = len(v)
    out = {"n": n}
    w = min(need, n)
    out["vol_pct120"] = round(float((v[-w:] <= v[-1]).mean()), 3)
    if len(v) >= 20 and v[-20:].std() > 0:
        out["vol_z20"] = round(float((v[-1] - v[-20:].mean()) / v[-20:].std()), 3)
    else:
        out["vol_z20"] = None
    if len(v) >= 60 and v[-60:].mean() > 0:
        out["ratio"] = round(float(v[-5:].mean() / v[-60:].mean()), 3)
    else:
        out["ratio"] = None
    return out

# ---------- 主回测 ----------
def run():
    hist = load("concept_hist.json")
    vol_map = load("concept_vol.json")
    wave = load("wave_state.json")
    macro_op = wave.get("operation", "build") or "build"
    print(f"概念数={len(hist)} 量能代理={len(vol_map)} 大级别op={macro_op}")

    # 全局交易日历（取概念日期并集，排序）
    cal = sorted({d for a in hist.values() for d in a["dates"]})
    cal = [d for d in cal if d >= "20250820"]
    # 回测时点：每 15 个交易日取一个；20d 需要余量>=25 交易日，60d 需要>=65
    # → 分两批：bt60(可算60日) 与 bt20(仅20日, 含 2026-06/07 低位段, 捕捉 LOW)
    bdates = []
    for i, d in enumerate(cal):
        if d < "20251201": continue
        if len(cal) - i <= 25: break
        if not bdates or i - cal.index(bdates[-1]) >= 15:
            bdates.append(d)
    bdates60 = [d for d in bdates if len(cal) - cal.index(d) > 65]
    print("回测时点(20d):", bdates)
    print("回测时点(60d):", bdates60)

    # 量能序列缓存
    vol_series_cache = {}
    for code, v in vol_map.items():
        if v.get("vol"):
            vol_series_cache[code] = pd.Series([float(x) for x in v["vol"]], index=pd.to_datetime(v["dates"]))

    groups = {}   # group -> {n: [(dt, ret)], 20: [...], 60: [...]}
    dates_meta = {}  # dt -> {pos_counts}
    low_fund_in = 0  # 统计 LOW 且资金流入的样本

    def add(grp, dt, r20, r60):
        g = groups.setdefault(grp, {"n": [], "20": [], "60": []})
        g["n"].append((dt, 1))
        if r20 is not None: g["20"].append((dt, r20))
        if r60 is not None: g["60"].append((dt, r60))

    for dt in bdates:
        t = pd.Timestamp(dt)
        dm = {"date": dt}
        for code, a in hist.items():
            ser = close_series(a); base = ser[ser.index <= t]
            if len(base) < 60 or float(base.min()) <= 0 or base.isna().any(): continue
            f = pc.position_features(base)
            if f is None: continue
            cls = pc.classify(f); pos = cls["position"]
            dm[pos] = dm.get(pos, 0) + 1
            net = net_series(a)
            fs = fund_at(net, dt)
            r20 = fwd_ret(ser, dt, 20); r60 = fwd_ret(ser, dt, 60)
            # 单位置
            add("pos_"+pos, dt, r20, r60)
            # 位置+资金
            add(pos+"_"+fs["dir"], dt, r20, r60)
            # 硬共振（现版逻辑）：macro 敏感性两种取值
            for mop, tag in ((macro_op, "cur"), ("build", "neutral")):
                action, sig = pc.resonance(pos, f, fs, None, None, mop, {})
                if pos == "HIGH":
                    add(f"res_{tag}_HIGH_减仓", dt, r20, r60) if "减仓" in action else add(f"res_{tag}_HIGH_观望健康", dt, r20, r60)
                elif pos == "LOW":
                    add(f"res_{tag}_LOW_埋伏", dt, r20, r60) if "低吸" in action else add(f"res_{tag}_LOW_观望", dt, r20, r60)
                # macro 敏感性：同一概念在 build 下与 cur 下 action 是否不同
                if pos == "HIGH":
                    a_cur, _ = pc.resonance(pos, f, fs, None, None, macro_op, {})
                    a_build, _ = pc.resonance(pos, f, fs, None, None, "build", {})
                    if a_cur != a_build:
                        add("macro_diff_HIGH", dt, r20, r60)
            if pos == "LOW" and fs["dir"] == "in":
                low_fund_in += 1
            # 量能子样本：概念在 vol_map 中且 upto 时有量能
            vs = vol_series_cache.get(code)
            if vs is not None:
                vf = vol_feat_at(vs, dt)
                if vf is not None:
                    add("vol_sample", dt, r20, r60)
                    vz = vf.get("vol_z20"); vp = vf.get("vol_pct120")
                    vol_top = bool((vz is not None and vz > 1.5) or (vp is not None and vp < 0.15 and f.get("vs_1y_high_pct",0) > -10))
                    vol_bot = bool(vp is not None and vp < 0.15)
                    if pos == "HIGH":
                        add("vol_HIGH_top" if vol_top else "vol_HIGH_notop", dt, r20, r60)
                    if vol_bot:
                        add("vol_bottom", dt, r20, r60)
        dates_meta[dt] = dm

    return groups, dates_meta, low_fund_in, bdates, bdates60

# ---------- 汇总 ----------
def summarize(groups, bdates):
    def stats(items):
        if not items: return None
        r = [x for _, x in items]
        r = np.array(r, dtype=float)
        return {"n": int(len(r)), "mean": round(float(r.mean()),2), "median": round(float(np.median(r)),2),
                "p25": round(float(np.percentile(r,25)),2), "p75": round(float(np.percentile(r,75)),2),
                "hit": round(float((r>0).mean()),3), "neg": round(float((r<0).mean()),3)}
    out = {}
    for grp, g in groups.items():
        out[grp] = {h: stats(g[h]) for h in ("n","20","60")}
        out[grp]["n"] = len(g["n"])
    return out

def fmt(s):
    if s is None: return "  -  "
    return f"{s['mean']:>6.2f}% {s['median']:>6.2f}% hit={s['hit']:.2f} (n={s['n']})"

def main():
    groups, dates_meta, low_fund_in, bdates, bdates60 = run()
    agg = summarize(groups, bdates)
    json.dump({"dates": bdates, "dates60": bdates60, "dates_meta": dates_meta, "low_fund_in": low_fund_in,
               "groups": agg}, open(os.path.join(DATA,"position_systematic_backtest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # ---- 打印 ----
    print()
    print("== 时点分布（HIGH/MID/LOW 计数；* = 可算60日） ==")
    for dt in bdates:
        m = dates_meta[dt]
        star = "*" if dt in bdates60 else " "
        print(f"  {dt}{star} HIGH={m.get('HIGH',0)} MID={m.get('MID',0)} LOW={m.get('LOW',0)}")
    print(f"  LOW且资金流入样本累计: {low_fund_in}")
    print()
    print("== 单位置（20日 / 60日前向） ==")
    for k in ("pos_HIGH","pos_MID","pos_LOW"):
        print(f"  {k:9} 20d: {fmt(agg[k]['20'])}")
        print(f"  {'':9} 60d: {fmt(agg[k]['60'])}")
    print()
    print("== 位置+资金 ==")
    for k in ("HIGH_in","HIGH_out","MID_in","MID_out","LOW_in","LOW_out"):
        if k in agg:
            print(f"  {k:9} 20d: {fmt(agg[k]['20'])}")
            print(f"  {'':9} 60d: {fmt(agg[k]['60'])}")
    print()
    print("== 硬共振 HIGH（现版 macro=%s vs neutral=build） ==" % (load("wave_state.json").get("operation")))
    for tag in ("cur","neutral"):
        for k in (f"res_{tag}_HIGH_减仓", f"res_{tag}_HIGH_观望健康"):
            if k in agg:
                print(f"  {k:28} 20d: {fmt(agg[k]['20'])}")
                print(f"  {'':28} 60d: {fmt(agg[k]['60'])}")
    print("  macro 敏感差异样本(HIGH cur!=build):", agg.get("macro_diff_HIGH",{}).get("n",0))
    print()
    print("== 硬共振 LOW（现版逻辑需 low_logic_agent，历史无 AI 逻辑 → 全部观望） ==")
    for tag in ("cur","neutral"):
        for k in (f"res_{tag}_LOW_埋伏", f"res_{tag}_LOW_观望"):
            if k in agg:
                print(f"  {k:28} 20d: {fmt(agg[k]['20'])}")
                print(f"  {'':28} 60d: {fmt(agg[k]['60'])}")
    print()
    print("== 量能子样本（40概念, 2026-03-24 起） ==")
    for k in ("vol_sample","vol_HIGH_top","vol_HIGH_notop","vol_bottom"):
        if k in agg:
            print(f"  {k:16} 20d: {fmt(agg[k]['20'])}")
            print(f"  {'':16} 60d: {fmt(agg[k]['60'])}")
    print()
    print("WROTE data/position_systematic_backtest.json")

if __name__ == "__main__":
    main()

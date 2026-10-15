# -*- coding: utf-8 -*-
"""eval_chain_contagion.py — 「链主破位 ⇒ 同链陪葬」有没有**增量信息**？（2026-10-15 首验）

狼大原话（帖 47288722）：
  · 2026-10-12「海外链会因为大光向下破位**加速陪葬**……机构但凡不想死，这里他们也要做个技术支撑」
  · 2026-10-09「中际旭创里机构**跌破年线强制止损**……**很多票在陪葬**」

测什么（三个可证伪的量）：
  ① **陪葬**：锚定链主「新破 20 日低位」当天，取**同链尚未破位的成员** ⇒ 后 5/10 日收益，
     与全市场同日中位数比（跑输＝陪葬成立）；
  ② **提前量**：这些成员自己「新破 20 日低位」的日期 − 锚定破位日（正数＝我们确实提前预警）；
  ③ **反事实**：同链**已经破位**的成员后续表现（若二者无差异 ⇒ “连坐”没有额外信息）。

用法::
    python jobs/eval_chain_contagion.py --start 20260701 --end 20260914 --hold 5,10
"""
import argparse
import collections
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import chain_contagion as CC  # noqa: E402


def fwd(series, date8, hold):
    ds = [r[0] for r in series]
    try:
        i = ds.index(date8)
    except ValueError:
        return None
    j = i + hold
    if j >= len(series):
        return None
    return series[j][1] / series[i][1] - 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260701")
    ap.add_argument("--end", default="20260914")
    ap.add_argument("--hold", default="5,10")
    args = ap.parse_args()
    holds = [int(x) for x in args.hold.split(",")]

    cs = CC.chains(args.end)
    if not cs:
        print("无链快照 ⇒ fail-open"); return 0
    codes = set()
    for c in cs:
        codes.update(c["members"]); codes.update(a["ts"] for a in c["anchors"])
    bars = CC.load_bars(codes)
    print("链数=%d 成员代码=%d 有日线的代码=%d" % (len(cs), len(codes), len(bars)))

    dates = sorted({d for s in bars.values() for d, _, _ in s if args.start <= d <= args.end})
    if not dates:
        print("区间内无交易日"); return 0

    # 全市场同日基线（同池中位数）
    def baseline(date8, hold, exclude):
        vals = [fwd(bars[c], date8, hold) for c in bars if c not in exclude]
        vals = [v for v in vals if v is not None]
        return st.median(vals) if vals else None

    rows = []
    for date8 in dates:
        for c in cs:
            for a in c["anchors"]:
                b = CC.broke(bars.get(a["ts"]) or [], date8)
                if b.get("cross_low") != date8:      # ★ 只用**当日首次下穿**去重（否则同一事件被记 5 次）
                    continue
                members = [m for m in c["members"] if m in bars and m != a["ts"]]
                memb_state = {m: CC.broke(bars[m], date8).get("broke") for m in members}
                intact = [m for m in members if not memb_state[m]]
                broken = [m for m in members if memb_state[m]]
                rows.append({"date": date8, "theme": c["theme"], "label": c["label"], "anchor": a["name"],
                             "intact": intact, "broken": broken, "members": members})

    if not rows:
        print("区间内无「链主新破 20 日低位」事件"); return 0
    print("锚定破位事件：%d 条（去重前）" % len(rows))

    for hold in holds:
        i_vals, b_vals, base_vals, leads = [], [], [], []
        for r in rows:
            base = baseline(r["date"], hold, set(r["members"]))
            if base is not None:
                base_vals.append(base)
            for m in r["intact"]:
                v = fwd(bars[m], r["date"], hold)
                if v is not None:
                    i_vals.append(v)
            for m in r["broken"]:
                v = fwd(bars[m], r["date"], hold)
                if v is not None:
                    b_vals.append(v)
            # 提前量：intact 成员自己破位日 − 事件日
            for m in r["intact"]:
                ds = [d for d, _, _ in bars[m]]
                after = [d for d in ds if d > r["date"]]
                cross = None
                for d in after[:20]:
                    if CC.broke(bars[m], d).get("broke"):
                        cross = d
                        break
                if cross:
                    leads.append((int(cross) - int(r["date"])))
                else:
                    leads.append(None)
        def fmt(v):
            return "n=%-5d 均值%+6.2f%% 中位%+6.2f%%" % (len(v), 100 * sum(v) / len(v), 100 * st.median(v)) if v else "n=0"
        print("\n=== hold=%d 日 ===" % hold)
        print("  ① 同链**未破**成员（连坐对象）：", fmt(i_vals))
        print("  ⑥ 同链**已破**成员（对照）    ：", fmt(b_vals))
        print("  市场同日基线（同池中位）      ：", fmt(base_vals))
        if i_vals and base_vals:
            print("  ⇒ 相对基线：均值 %+.2fpp / 中位 %+.2fpp（负＝确实跑输＝陪葬成立）" % (
                100 * (sum(i_vals) / len(i_vals) - sum(base_vals) / len(base_vals)),
                100 * (st.median(i_vals) - st.median(base_vals))))
        if i_vals and b_vals:
            print("  ⇒ 未破 vs 已破：中位差 %+.2fpp（≈0 ⇒ “连坐”没有额外信息）" % (
                100 * (st.median(i_vals) - st.median(b_vals))))
        # ④ 聚焦他**具体那条**：大光(中际旭创 300308.SZ) 破位 ⇒ 国产半导体链成员（跨链陪葬）
        semi_kw = ["半导体材料", "半导体设备", "光刻胶", "存储芯片", "AI芯片"]
        mega = "300308.SZ"
        cross_vals, cross_base = [], []
        for date8 in dates:
            if CC.broke(bars.get(mega) or [], date8).get("cross_low") != date8:
                continue
            for c in cs:
                if not CC._kw_hit(c["concepts"], semi_kw):
                    continue
                for m in c["members"]:
                    if m in bars:
                        v = fwd(bars[m], date8, hold)
                        if v is not None:
                            cross_vals.append(v)
            b0 = baseline(date8, hold, set())
            if b0 is not None:
                cross_base.append(b0)
        if cross_vals and cross_base:
            print("  ④ 大光破位 ⇒ 国产半导体链（跨链）：", fmt(cross_vals),
                  "| 基线", fmt(cross_base), "⇒ 中位差 %+.2fpp" % (100 * (st.median(cross_vals) - st.median(cross_base))))
        ok = [x for x in leads if x is not None]
        if ok:
            print("  ② 提前量（成员自己破位 − 锚定破位，交易日）：n=%d 中位 %d 天（正＝我们提前）" % (
                len(ok), int(st.median(ok))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

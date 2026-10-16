# -*- coding: utf-8 -*-
"""E1d 步骤 B：用**他报出的仓位数字**（连续量）检验 E1c 的六类「减仓窗口」线索。

为什么换到这条线：E1c 用「一句话 = 一个动作标签」做 ground truth，被他「讲操作时 68~71% 本就在降风险」
的选择性偏差压死（lift 全在噪声带 ✗）。仓位数字是**连续量**，而且「减仓事件」可直接由序列算出
（Δ ≤ −5pp）⇒ 不需要我判句子的方向 ✓，同一条发言的信息量也大得多。

三个检验（**先声明，后看数**；同一套线索、同一份仓位序列 ✓）：

  T1 日度**水平**检验（无跨期混淆 ✓）
     观测日 t 命中线索 X ⇒ 比较他当日报出的仓位水平 vs 未命中日（中位/均值 + Welch t 双侧 p）。
     读法：他是不是真在「事件日」把仓位放低？（低 ⇒ 支持「减仓窗口」）

  T2 **变动**检验（跨期区间口径）
     把相邻两次观测 (t_i, t_{i+1}] 的 Δ = pos(t_{i+1}) − pos(t_i) 归给这段区间；
     区间内**含**线索日 X ⇒ 比较 Δ 分布 vs 不含的区间。
     ⚠️ 已知混淆：区间越长越容易「含线索日」⇒ 另报 **gap ≤ 5 交易日** 子集（长度混淆已被压住 ✓）。

  T3 **precision / lift**（与 E1c 同语言，便于对比 ✓）
     「减仓事件」= Δ ≤ −5pp（客观阈值，非我判句 ✗）；P(减仓 | 区间含线索 X) vs 基础率 P(减仓)；
     lift = precision / 基础率；同时报标记区间数、召回（覆盖了多少次减仓）。

参数取原则性值（不按标注集调 ✗）：CHANGE_PP = 5pp（一个整数档的量级）、短区间闸 gap ≤ 5 交易日
（≈ 一周，与「事件窗口」同尺度）、显著性用 Welch t 双侧（不做单侧挑方向）。

用法：.venv/bin/python jobs/eval_pos_events.py     → data/_pos_event_eval.json
"""
import bisect
import collections
import json
import math
import os
import statistics
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(ROOT)
import wave_ladder as W                        # noqa: E402
from eval_side_trim_windows import Cal, clue_of, welch   # E1c 的交易日历/线索定义，**复用不复制** ✓

CHANGE_PP = 5.0
SHORT_GAP = 5          # 交易日


def load_series() -> List[dict]:
    d = json.load(open(os.path.join("data", "_wolf_pos_series.json"), encoding="utf-8"))
    return d["series"]


def build(cal: Cal, ser: List[dict]) -> Tuple[List[dict], List[dict]]:
    """观测日（带链/线索）与区间（Δ仓位）。发帖日可能是周末 ⇒ 归位到最后交易日（同 E1c 口径 ✓）。"""
    obs = []
    for s in ser:
        k = bisect.bisect_right(cal.d8, s["date"].replace("-", "")) - 1
        if k < 0:
            continue
        obs.append({"date": s["date"], "k": k, "txn": cal.d8[k], "pos": s["pos"], "quote": s["quote"],
                    "year": s["date"][:4], "clues": [n for n, v in clue_of(cal, k).items() if v]})
    iv = []
    for a, b in zip(obs, obs[1:]):
        iv.append({"from": a["date"], "to": b["date"], "k_from": a["k"], "k_to": b["k"],
                   "gap": b["k"] - a["k"], "delta": b["pos"] - a["pos"],
                   "mid_clues": sorted({n for k in range(a["k"] + 1, b["k"] + 1)
                                        for n, v in clue_of(cal, k).items() if v}),
                   "year": b["year"]})
    return obs, iv


def report(names: List[str], obs: List[dict], iv: List[dict], tag: str) -> dict:
    short = [x for x in iv if x["gap"] <= SHORT_GAP]
    base_cut = sum(1 for x in short if x["delta"] <= -CHANGE_PP) / len(short) if short else float("nan")
    print("\n=== %s ｜ 观测日 %d（%s~%s）｜ 区间 %d（gap≤%d 的 %d）｜ 基础率 P(Δ≤−%.0fpp) = %.3f ===" % (
        tag, len(obs), obs[0]["date"], obs[-1]["date"], len(iv), SHORT_GAP, len(short), CHANGE_PP, base_cut))
    print("%-20s %6s %8s %8s %7s | %6s %8s %7s | %6s %8s %7s %7s" % (
        "线索", "水平日", "中位%", "均值%", "p", "区间", "Δ均值", "p", "标记", "precision", "lift", "召回"))
    out = []
    non_clue = [o for o in obs]
    for nm in names:
        on = [o for o in obs if nm in o["clues"]]
        off = [o for o in obs if nm not in o["clues"]]
        if not on:
            print("%-20s %6d  —        —        —     | %6d  —        —       | %6d  —         —       —" % (
                nm, 0, 0, 0))
            continue
        t, p = welch([o["pos"] for o in on], [o["pos"] for o in off])
        iv_on = [x for x in short if nm in x["mid_clues"]]
        iv_off = [x for x in short if nm not in x["mid_clues"]]
        t2, p2 = welch([x["delta"] for x in iv_on], [x["delta"] for x in iv_off])
        n_cut = sum(1 for x in iv_on if x["delta"] <= -CHANGE_PP)
        prec = n_cut / len(iv_on) if iv_on else float("nan")
        lift = prec / base_cut if iv_on and base_cut else float("nan")
        tot_cut = sum(1 for x in short if x["delta"] <= -CHANGE_PP)
        rec = n_cut / tot_cut if tot_cut else float("nan")
        print("%-20s %6d %8.1f %8.1f %7.3f | %6d %8.2f %7.3f | %6d %8.3f %7.3f %7.3f" % (
            nm, len(on), statistics.median([o["pos"] for o in on]), sum(o["pos"] for o in on) / len(on), p,
            len(iv_on), (sum(x["delta"] for x in iv_on) / len(iv_on)) if iv_on else float("nan"), p2,
            len(iv_on), prec, lift, rec))
        out.append({"clue": nm, "obs_days": len(on),
                    "pos_median_on": statistics.median([o["pos"] for o in on]),
                    "pos_median_off": statistics.median([o["pos"] for o in off]) if off else float("nan"),
                    "pos_mean_on": sum(o["pos"] for o in on) / len(on),
                    "pos_mean_off": (sum(o["pos"] for o in off) / len(off)) if off else float("nan"),
                    "pos_p": p, "iv_days": len(iv_on),
                    "delta_mean_on": (sum(x["delta"] for x in iv_on) / len(iv_on)) if iv_on else float("nan"),
                    "delta_mean_off": (sum(x["delta"] for x in iv_off) / len(iv_off)) if iv_off else float("nan"),
                    "delta_p": p2, "cuts": n_cut, "precision": prec, "lift": lift, "recall": rec})
    print("（参照：全部观测的水平中位 %.0f%%；短区间 Δ 中位 %+.1fpp、减仓 %d 次 / 加仓 %d 次）" % (
        statistics.median([o["pos"] for o in obs]),
        statistics.median([x["delta"] for x in short]) if short else float("nan"),
        sum(1 for x in short if x["delta"] <= -CHANGE_PP),
        sum(1 for x in short if x["delta"] >= CHANGE_PP)))
    return {"obs_days": len(obs), "intervals": len(iv), "short_intervals": len(short),
            "base_cut": base_cut, "clues": out}


def main() -> int:
    rows = W.load_rows()
    cal = Cal(rows)
    ser = load_series()
    obs, iv = build(cal, ser)
    ev_by_date = {e["date"]: e for e in W.walk(rows)}
    names = sorted({n for o in obs for n in o["clues"]})
    print("仓位序列：观测日 %d（%s ~ %s）｜区间 %d ｜ 线索种类 %d" % (
        len(obs), obs[0]["date"], obs[-1]["date"], len(iv), len(names)))
    out = {"all": report(names, obs, iv, "全样本 2016-2026")}
    obs25 = [o for o in obs if o["year"] >= "2025"]
    iv25 = [x for x in iv if x["year"] >= "2025"]
    out["since2025"] = report(names, obs25, iv25, "2025 起（与 E1b/E1c 标注期重叠）")
    print("\n=== 他仓位最高的 8 个观测日 / 最低的 8 个（可复核锚点 ✓）===")
    for o in sorted(obs, key=lambda x: -x["pos"])[:8]:
        print("  %s %3.0f%% | %s" % (o["date"], o["pos"], o["quote"][:56]))
    for o in sorted(obs, key=lambda x: x["pos"])[:8]:
        print("  %s %3.0f%% | %s" % (o["date"], o["pos"], o["quote"][:56]))
    # 附加（正交且便宜 ✓）：把观测日按**我们链的档**分组，看他的**实际仓位水平**是否与档位同向。
    # 注意问法与 E1/E1b 不同：不是「我们的档能否预测他的动作」，而是「我们的档是否与他的风险暴露一致」✓
    print("\n=== 附加：他报出的仓位 × 我们链的档（同一天）===")
    by_op = collections.defaultdict(list)
    for o in obs:
        e = ev_by_date.get(o["txn"])
        if e:
            by_op[e["operation"]].append(o["pos"])
    for op in ("side", "t_only", "defense"):
        v = by_op.get(op) or []
        if v:
            print("  我们=%-8s n=%3d ｜ 他仓位 中位 %3.0f%%  均值 %5.1f%%  最低 %3.0f%%" % (
                op, len(v), statistics.median(v), sum(v) / len(v), min(v)))
    sub = collections.defaultdict(list)
    for o in obs:
        e = ev_by_date.get(o["txn"])
        if e and o["year"] >= "2025":
            sub[(e["operation"], e["name"])].append(o["pos"])
    print("  2025 起细分：")
    for (op, nm), v in sorted(sub.items(), key=lambda x: -len(x[1])):
        print("    %-8s %-10s n=%3d ｜ 他仓位 中位 %3.0f%% 均值 %5.1f%%" % (
            op, nm, len(v), statistics.median(v), sum(v) / len(v)))
    out["by_our_op"] = {op: {"n": len(v), "median": statistics.median(v), "mean": sum(v) / len(v)}
                        for op, v in by_op.items()}
    json.dump(out, open(os.path.join("data", "_pos_event_eval.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n→ data/_pos_event_eval.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

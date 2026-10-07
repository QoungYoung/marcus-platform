#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语料补齐 A/B 对照报告：同窗口两臂的**机制指标 + 收益路径**对比。

口径：两臂同窗口、同沙箱种子、各自独立账户/沙箱根；收益差含 LLM 采样方差（每臂 1 次，σ≈2pp 量级），
故**主结论看机制指标**：单笔委托量、暴露率、卖腿触发数、三道门拦阻数、被拦金额。
用法：.venv/bin/python jobs/bt_corpus_ab_report.py [--start 20260105 --end 20260130]
"""
from __future__ import annotations
import argparse, glob, json, os, re, statistics, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs"))
import bt_cmp_gens as C  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def arm_metrics(arm: str, d0: str, d1: str) -> dict:
    _cands = [os.path.join(REPO, "data/_bt_corpus_ab", "shared", "_arm_" + arm),
              os.path.join(REPO, "data/_bt_corpus_ab", "shared_" + arm, "_arm_" + arm)]
    sb = next((c for c in _cands if os.path.isdir(c)), _cands[0])
    cur = [c for c in (C.rebuild(os.path.join(sb, "prod_2026*.json")) or [])
           if d0 <= c["day"] <= d1]
    m = {"arm": arm, "days": len(cur), "equity": cur[-1]["equity"] if cur else None,
         "ret_pct": ((cur[-1]["equity"] / 250000 - 1) * 100) if cur else None,
         "curve": [(c["day"], round(c["equity"])) for c in cur]}
    # 成交：单笔量/暴露/买卖笔数
    seen, buys, sells = set(), [], []
    for f in sorted(glob.glob(os.path.join(sb, "prod_2026*.json"))):
        j = json.load(open(f, encoding="utf-8"))
        if not (d0 <= str(j.get("day")) <= d1):
            continue
        for t in (j.get("trades") or []):
            if t.get("id") in seen: continue
            seen.add(t.get("id"))
            (buys if t.get("direction") == "买入" else sells).append(t)
    amts = [abs(float(t.get("amount") or 0)) for t in buys]
    m["buys"] = len(buys); m["sells"] = len(sells)
    m["buy_notional"] = sum(amts)
    m["buy_median"] = statistics.median(amts) if amts else 0
    m["buy_under5k_pct"] = (100 * sum(1 for a in amts if a < 5000) / len(amts)) if amts else 0
    # 暴露率（逐日均值）
    if cur:
        exps = [100 - (c["cash"] / (c["cash"] + c["mv"]) * 100 if (c["cash"] + c["mv"]) else 0) for c in cur]
        m["exposure_pct"] = statistics.mean(exps)
    # 触发写行：按类型统计（persist 臂的主指标 —— 看行数与 blocked 占比是否下降）
    rows_by_kind = {}
    blocked = total_rows = 0
    for f in sorted(glob.glob(os.path.join(sb, "prod_2026*.json"))):
        j = json.load(open(f, encoding="utf-8"))
        if not (d0 <= str(j.get("day")) <= d1):
            continue
        for t in (j.get("triggers") or []):
            k = str(t.get("event_type"))
            rows_by_kind[k] = rows_by_kind.get(k, 0) + 1
            total_rows += 1
            if str(t.get("status")) == "blocked":
                blocked += 1
    m["trig_rows"] = total_rows
    m["trig_blocked_pct"] = (100.0 * blocked / total_rows) if total_rows else 0.0
    m["trig_persist"] = sum(rows_by_kind.get(k, 0) for k in
                            ("custom_vwap_sell", "custom_support_sell", "high_sell"))
    m["trig_top"] = sorted(rows_by_kind.items(), key=lambda kv: -kv[1])[:5]
    m["rows_per_day"] = (total_rows / max(len(cur), 1)) if cur else 0

    # trigger / 拦阻计数（看日志标志行）
    logs = sorted(glob.glob(os.path.join(sb, "prod_2026*.log")))
    cnt = {"语料前置门拦买腿": 0, "两分法": 0, "止损扫描异常": 0, "无底仓卖腿不送审": 0}
    gate_skip = 0
    for f in logs:
        d = os.path.basename(f)[5:13]
        if not (d0 <= d <= d1): continue
        txt = open(f, encoding="utf-8", errors="replace").read()
        cnt["语料前置门拦买腿"] += len(re.findall(r"语料前置门拦买腿", txt))
        cnt["两分法"] += len(re.findall(r"两分法", txt))
        cnt["止损扫描异常"] += len(re.findall(r"止损扫描异常（第", txt)) + len(re.findall(r"止损扫描异常 .*（第", txt))
        cnt["无底仓卖腿不送审"] += len(re.findall(r"无底仓卖腿不送审", txt))
        m2 = re.findall(r"gate_corpus_skip=(\d+)", txt)
        if m2: gate_skip += int(m2[-1])
        m3 = re.findall(r"命中.*?两分法", txt)
        cnt["两分法"] += len(m3)
    cnt["gate_corpus_skip"] = gate_skip
    m["blocks"] = cnt
    return m


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260130")
    ap.add_argument("--arms", default="off,corpus,persist"); a = ap.parse_args()
    arms = a.arms.split(",") if a.arms else ["off", "corpus", "persist"]
    rows = [arm_metrics(x, a.start, a.end) for x in arms]
    if not any(r["days"] for r in rows):
        print("还没有 A/B 产物（data/_bt_corpus_ab/shared/_arm_{off,corpus,persist}）——先跑 bash jobs/bt_corpus_ab.sh")
        return 2
    print("══ 语料补齐 同窗口对照（%s → %s）" % (a.start, a.end))
    names = ["%-24s" % "指标"] + ["%-15s" % ("arm=" + r["arm"]) for r in rows]
    hdr = " ".join(names); print(hdr); print("-" * len(hdr))
    def line(name, k, fmt="%s"):
        cells = []
        for r in rows:
            v = r.get(k)
            cells.append("%-15s" % ((fmt % v) if v is not None else "-"))
        print("%-24s %s" % (name, " ".join(cells)))
    line("交易日", "days", "%.0f")
    line("期末权益", "equity", "%.0f")
    line("收益%", "ret_pct", "%+.2f")
    line("平均暴露%", "exposure_pct", "%.1f")
    line("买入笔数", "buys", "%.0f")
    line("买入总额", "buy_notional", "%.0f")
    line("单笔中位(元)", "buy_median", "%.0f")
    line("单笔<5000占比%", "buy_under5k_pct", "%.0f")
    line("触发写行(总)", "trig_rows", "%.0f")
    line(" 其中持续腿三类", "trig_persist", "%.0f")
    line(" 行/交易日", "rows_per_day", "%.0f")
    line("blocked 占比%", "trig_blocked_pct", "%.1f")
    for k in ("语料前置门拦买腿", "两分法", "gate_corpus_skip", "无底仓卖腿不送审", "止损扫描异常"):
        print("%-24s %s" % (k, " ".join("%-15s" % r["blocks"].get(k) for r in rows)))
    print("\n曲线对照（日：off / on）")
    cs = {r["arm"]: dict(r["curve"]) for r in rows}
    for d in sorted(set().union(*[set(c) for c in cs.values()])):
        print("  %s  " % d + "  ".join("%s=%s" % (k, v.get(d, "-")) for k, v in cs.items()))
    print("\n⚠️ 每臂只 1 次 ⇒ 收益差含 LLM 采样方差（σ≈2pp 量级）：**主结论看机制指标**"
          "（单笔量/暴露/卖腿触发/三道门拦阻），收益差仅作参考。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

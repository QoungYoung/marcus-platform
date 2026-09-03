# -*- coding: utf-8 -*-
"""judge_wolf_event_alignment.py — 逐事件买点一致率·唯一事件判定 v2（诚实口径）

目的：修复盘#1/#3——不再把“动作通道覆盖率(93.3%)”或“窗口内任意一次动作”当作一致率。
规则：
  1) 只有具备 5min 动作回放的事件（E05/E06/E08/E12/E13，每事件 3-6 只代理股）才进入一致率分子；
  2) 每只代理股用 stepwise_253_backtest_B(253 B语义+254首次+后3日≤2次回补) 的 same/within5 判定；
  3) 事件级 = 该事件代理股中“±5日有动作”的覆盖率(coverage)，不因多次动作重复计分；
  4) E01-E04/E07/E09-E11/E14/E15 无 5min 动作回放 → 记为 no_5m_replay，只输出 P3 通道可达性(不进入一致率)。
输出: data/wolf_event_alignment_v2.json + stdout
"""
import json, os, collections

DATA = os.environ.get("DATA_DIR", "data")

def load(p):
    try:
        return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
    except Exception:
        return None

def main():
    p3 = load("p3_tier_backtest.json") or {}
    stepB = load("stepwise_253_backtest_B.json") or []
    p3_by = {e["id"]: e for e in p3.get("events") or []}
    rows_by = collections.defaultdict(list)
    for r in stepB:
        if not r.get("error"):
            rows_by[r["event"]].append(r)
    events = []
    for eid in ["E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E09", "E10",
                "E11", "E12", "E13", "E14", "E15"]:
        p = p3_by.get(eid) or {}
        rr = rows_by.get(eid, [])
        base = {"event": eid, "wave_op": p.get("wave_op"),
                "p3_channel": bool(p.get("allowed_intents")),
                "p3_allowed_intents": p.get("allowed_intents") or []}
        if not rr:
            events.append({**base, "measurable_5m": False,
                           "verdict": "no_5m_replay(不计一致率, 仅P3通道参考)"})
            continue
        same = sum(1 for r in rr if r["same_day_action"])
        w5 = sum(1 for r in rr if r["within5"])
        n = len(rr)
        events.append({**base, "measurable_5m": True, "n_5m_rows": n,
                       "same_day_n": same, "within5_n": w5,
                       "same_day_coverage": round(same / n, 3),
                       "within5_coverage": round(w5 / n, 3),
                       "event_verdict": "aligned" if w5 / n >= 0.5 else "partial",
                       "reason": "±5日动作覆盖率%s" % (w5 / n)})
    meas = [e for e in events if e.get("measurable_5m")]
    # 个股级主线内切换通道（E10 MVP）：链级卖旧(出货周期) + 个股选买 → switch_executed 视为对齐
    sw = load("switch_stock_backtest.json") or {}
    switch_ok = bool(sw.get("switch_executed"))
    if switch_ok:
        for e in meas:
            if e["event"] == sw.get("event"):
                e["within5_n"] = e["n_5m_rows"]
                e["within5_coverage"] = 1.0
                e["event_verdict"] = "aligned"
                e["reason"] = "主线内切换个股级(switch_stock) 通过: 链级卖旧+个股选买"
    # 敏感性：exit+无底仓时生产 P3 禁止任何买入（E06 若不放行则 3 只代理都不算对齐）
    e06 = next((e for e in meas if e["event"] == "E06"), None)
    sens = {}
    if e06:
        sens["E06_no_base_exit_blocked"] = {
            "rows": e06["n_5m_rows"], "same_day_n": 0, "within5_n": 0,
            "note": "当前生产 P3 exit+无底仓禁止 refill/t_refill → E06 代理动作(stepwise B 假设有底仓)全部不可执行，覆盖率 3/3→0/3；若放行则一致性+3/3但sys T+5约-2.6%（Wolf -4.65，同为负收益）"
        }
    # 数据覆盖缺口说明
    sens["E02"] = {"note": "2025-09-10 无 5min/代理股票数据，probe≤3% 放行影响只能等 buy_point_log 实盘观察"}

    tot_rows = sum(e["n_5m_rows"] for e in meas)
    same_rows = sum(e["same_day_n"] for e in meas)
    w5_rows = sum(e["within5_n"] for e in meas)
    out = {
        "method": "wolf_event_alignment_v2 (唯一事件判定; 仅可5min回放事件计一致率)",
        "generated": "2026-09-03",
        "events": events,
        "summary": {
            "measurable_events": len(meas),
            "rows": tot_rows,
            "same_day_rows": same_rows,
            "same_day_rate": round(same_rows / max(tot_rows, 1), 3),
            "within5_rows": w5_rows,
            "within5_rate": round(w5_rows / max(tot_rows, 1), 3),
            "events_aligned_binary": sum(1 for e in meas if e.get("event_verdict") == "aligned"),
            "avg_within5_coverage_events": round(sum(e["within5_coverage"] for e in meas) / len(meas), 3),
        },
        "sensitivity": sens,
        "caveats": ["E01-E04/E07/E09-E11/E14/E15 无5min动作回放→不计一致率；代理股≠实盘；253 B语义+分步小仓假设",
                    "事件一致=代理股±5日动作覆盖率≥50%；同一天多次动作不重复加分"],
    }
    path = os.path.join(DATA, "wolf_event_alignment_v2.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    for e in meas:
        print(e["event"], "rows", e["n_5m_rows"], "same", e["same_day_n"], "w5", e["within5_n"],
              "coverage", e["within5_coverage"], "->", e["event_verdict"], flush=True)
    print("WROTE", path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

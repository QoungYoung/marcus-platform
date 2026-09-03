# -*- coding: utf-8 -*-
"""score_buy_point_alignment.py — 逐事件买点一致率统一评测（v1，诚实基线）

口径（用户选定：事件级行为一致 ±5交易日；同时保留严格同日对照）:
  1) 盘口口径(数据子集 E05/E06/E08/E12/E13, data/crowding_pit/wolf_dip254_backtest.json):
     254 触发同日率 / ±5交易日命中率(按触发样本)。
  2) 动作通道口径(E01-E15, data/p3_tier_backtest.json):
     old=pass=1 / partial=0.5 / block=0；P3=P3 存在匹配 intent 通道=1，否则同 old。
     ⚠️ 这是“行为通道可达性”不是逐日成交对齐；同日/±5日需逐股重放（见 docs/buy-point-alignment-metric.md）。
输出: data/buy_point_alignment_score.json + stdout
"""
import json
import os

DATA = os.environ.get("DATA_DIR", "data")

def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None

def main():
    out = {"method": "buy-point alignment v1", "date": "2026-09-03", "metrics": {}}
    # 1) 盘口 254（E05/E06/E08/E12/E13 代理股）
    bt = load(os.path.join(DATA, "crowding_pit", "wolf_dip254_backtest.json")) or []
    trig = [r for r in bt if isinstance(r.get("offset_days"), (int, float))]
    same = [r for r in bt if r.get("aligned_same_day")]
    within5 = [r for r in trig if abs(int(r["offset_days"])) <= 5]
    out["metrics"]["strict_same_day_254"] = {"triggers": len(trig), "rows": len(bt),
             "same_day_n": len(same), "same_day_rate": round(len(same) / max(len(bt), 1), 3)}
    out["metrics"]["within_5d_254"] = {"triggers": len(trig), "rows": len(bt),
             "within5_n": len(within5),
             "within5_of_triggered": round(len(within5) / max(len(trig), 1), 3),
             "within5_of_all_rows": round(len(within5) / max(len(bt), 1), 3),
             "offset_days": sorted({int(r["offset_days"]) for r in trig})}
    # 1b) 趋势线回补触发（E05/E06/E08/E12/E13，24 代理股；站上MA20且MA20>MA60且前收在MA20下）
    tr = load(os.path.join(DATA, "trend_refill_backtest.json")) or []
    if tr:
        import collections as _c
        byev = _c.defaultdict(list)
        for r in tr:
            byev[r["event"]].append(r)
        out["metrics"]["trend_refill_trigger"] = {
            "rows": len(tr), "triggered": sum(1 for r in tr if r["trigger_date"]),
            "same_day": sum(1 for r in tr if r["aligned_same_day"]),
            "within3": sum(1 for r in tr if r["within3"]),
            "within5": sum(1 for r in tr if r["within5"]),
            "by_event_within5": {k: sum(1 for r in v if r["within5"]) for k, v in sorted(byev.items())},
            "note": "日线代理：站上MA20(且MA20>MA60)回补触发；E12低吸型5/6对齐，E05/E13趋势内加仓型0——需另做缩量回踩/趋势内加仓触发"
        }
    # 1c) 253 全护栏 + 254后3日分步回补（E05/E06/E08/E12/E13, 24 代理股, 5min）
    sw = load(os.path.join(DATA, "stepwise_253_backtest.json")) or []
    ok = [r for r in sw if not r.get("error")]
    if ok:
        import collections as _c2
        byev2 = _c2.defaultdict(list)
        for r in ok:
            byev2[r["event"]].append(r)
        kinds = _c2.Counter(a["kind"] for r in ok for a in r.get("actions") or [])
        def _avg(xs):
            xs=[x for x in xs if x is not None]
            return round(sum(xs)/len(xs),2) if xs else None
        out["metrics"]["stepwise_253_guardrails"] = {
            "rows": len(ok), "same_day": sum(r["same_day_action"] for r in ok),
            "within3": sum(r["within3"] for r in ok), "within5": sum(r["within5"] for r in ok),
            "wolf_T5_avg": _avg([r["wolf_T5"] for r in ok]), "sys_T5_avg": _avg([r["sys_T5_mean"] for r in ok]),
            "action_kinds": dict(kinds),
            "by_event": {k: {"n": len(v), "same": sum(r["same_day_action"] for r in v),
                             "within5": sum(r["within5"] for r in v)} for k, v in sorted(byev2.items())},
            "note": "护栏: 253需个股bar.close>cumVWAP/非跌停/09:45-14:40; 254窗口内首次+后3日最多2次回补; ±5日=66.7%(16/24)高于254首次口径37.5%, 但E08同日仅2/6(护栏把raw 253 6/6降到2)"
        }
    # 2) 动作通道 E01-E15（P3 v0 + probe 裁决后）
    p3 = load(os.path.join(DATA, "p3_tier_backtest.json")) or {}
    evs = p3.get("events") or []
    old_w = sum(1.0 if e["old"] == "pass" else (0.5 if e["old"] == "partial" else 0.0) for e in evs)
    p3_w = sum(1.0 if e["old"] == "pass" else (1.0 if e.get("allowed_intents") else (0.5 if e["old"] == "partial" else 0.0)) for e in evs)
    out["metrics"]["action_channel_E01E15"] = {"events": len(evs), "note": "动作通道覆盖率(旧pass=1/partial=0.5/block=0 vs P3有匹配intent通道=1)",
             "old_weighted": round(old_w / max(len(evs), 1), 3), "p3_weighted": round(p3_w / max(len(evs), 1), 3),
             "old_pass_partial_block": {"pass": sum(1 for e in evs if e["old"] == "pass"),
                                        "partial": sum(1 for e in evs if e["old"] == "partial"),
                                        "block": sum(1 for e in evs if e["old"] == "block")},
             "p3_channel_events": sum(1 for e in evs if e.get("allowed_intents"))}
    out["caveats"] = ["严格同日=4%(1/24)、并253约29%来自5min回放；±5日逐日对齐需候选/执行历史重放，当前数据不足，先以动作通道口径衡量 P3 贡献",
                      "动作通道口径不算 rotation B3/手动T通道的部分匹配，是保守口径"]
    path = os.path.join(DATA, "buy_point_alignment_score.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2500])
    print("WROTE", path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

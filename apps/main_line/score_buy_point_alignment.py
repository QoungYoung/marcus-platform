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

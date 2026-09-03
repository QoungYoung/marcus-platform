# -*- coding: utf-8 -*-
"""backtest_p3_tiers.py — P3 三仓档位 E01-E15 事件级回测（纯规则层，Step A）。

输入：内嵌 E01-E15 Wolf 科技入场事件表（来自 .dsh-tmp/tech_entry_events.json + tech_entry_system_rows.json 的 wave op）。
判定：position_tier.three_tier_gate（config/p3_position_tiers.json v0）。
输出：data/p3_tier_backtest.json + stdout 摘要。
说明：has_base/t_universe 为语义假设（回补/加仓=已有底仓或属做T宇宙），不代表对账单；结果用于验证三仓档位能否补上“回补/加厚/T资格”通道，不是收益回测。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # repo root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))      # apps/main_line
try:
    from backend.app.services import position_tier as pt
except Exception:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
    from app.services import position_tier as pt

DATA = os.environ.get("DATA_DIR", "data")

# id/date/kind/wolf op/sub/has_base/t_universe/rel_low/old(old系统口径: pass=放行 partial=部分(多靠rotation) block=不会新建)
EVENTS = [
    dict(id="E01", date="2025-08-21", kind="新建", wolf="初入低位科技(存储DML+AI终端华勤)", op="build", sub="3-3", hb=False, tu=False, rel=False, old="pass"),
    dict(id="E02", date="2025-09-10", kind="新建", wolf="只买一点CPO陪同(小仓试盘)", op="t_only", sub="3-4", hb=False, tu=False, rel=False, old="block"),
    dict(id="E03", date="2025-10-28", kind="回补", wolf="指数下来继续买科技(3936-3960回补)", op="build", sub="3-3", hb=False, tu=True, rel=False, old="pass"),
    dict(id="E04", date="2025-10-30", kind="切换加仓", wolf="半导体设备ETF换入(科创100调过去)", op="build", sub="3-3", hb=True, tu=True, rel=False, old="pass"),
    dict(id="E05", date="2025-12-09", kind="加仓", wolf="加深两只液冷(已有底仓)", op="t_only", sub="3-4", hb=True, tu=True, rel=False, old="block"),
    dict(id="E06", date="2025-12-31", kind="回补", wolf="4000下买回AI硬(搏旭创回水)", op="exit", sub="3-5", hb=False, tu=True, rel=False, old="block"),
    dict(id="E07", date="2026-01-05", kind="加仓/主做", wolf="本月主做半导体", op="build", sub="3-3", hb=True, tu=True, rel=False, old="pass"),
    dict(id="E08", date="2026-01-14", kind="低吸", wolf="半导体设备ETF不清仓+把握每次低吸", op="exit", sub="3-5", hb=True, tu=True, rel=False, old="block"),
    dict(id="E09", date="2026-02-28", kind="主线内切换", wolf="减高位科技换低位科技(封测等)", op="exit", sub="3-5", hb=True, tu=True, rel=True, old="partial"),
    dict(id="E10", date="2026-03-19", kind="主线内切换", wolf="海外链→国算电源(麦米清出)", op="defense", sub="4-1", hb=True, tu=True, rel=True, old="partial"),
    dict(id="E11", date="2026-04-22", kind="主线内切换", wolf="设备→材料(设备最硬已新高)", op="exit", sub="3-5", hb=True, tu=True, rel=True, old="partial"),
    dict(id="E12", date="2026-06-05", kind="切换低吸", wolf="光45→18%内慢慢低吸半导体", op="defense", sub="4-1", hb=False, tu=True, rel=False, old="block"),
    dict(id="E13", date="2026-07-08", kind="加仓", wolf="继续加仓国产算力(已有方向)", op="defense", sub="4-3", hb=True, tu=True, rel=False, old="block"),
    dict(id="E14", date="2026-07-23", kind="调仓", wolf="4-3调仓国算(以ETF为主/穿越)", op="t_only", sub="4-4", hb=True, tu=True, rel=False, old="partial"),
    dict(id="E15", date="2026-08-12", kind="T仓回补", wolf="买回半导体保T空间和仓位", op="t_only", sub="4-4", hb=False, tu=True, rel=False, old="partial"),
]


def checked_intents(ev):
    kind = ev["kind"]
    if kind == "新建":
        return ["new_base"]
    if kind == "回补":
        return ["refill_base"]
    if kind in ("低吸", "T仓回补"):
        return ["t_refill", "refill_base"]
    if kind in ("加仓/主做", "加仓"):
        return ["add_base", "t_refill"]
    return ["add_base", "refill_base", "t_refill"]  # 切换/调仓


def main():
    out_rows = []
    summary = {"old_pass": 0, "old_partial": 0, "old_block": 0,
               "p3_gain_from_block": 0, "p3_gain_from_partial": 0, "still_block": []}
    for ev in EVENTS:
        rows_dec = []
        any_allowed = False
        allowed_intents = []
        for intent in checked_intents(ev):
            d = pt.three_tier_gate(wave_state={"operation": ev["op"], "sub_level": ev["sub"], "level": "d"},
                                   intent=intent, has_base=ev["hb"], t_universe=ev["tu"],
                                   rel_low=ev["rel"], mainline_dir=True)
            rows_dec.append(d)
            if d["intent_allowed"]:
                any_allowed = True
                allowed_intents.append("%s≤%s%%" % (intent, d["cap_pct"]))
        p3 = "P3通道✅(%s)" % "+".join(allowed_intents) if any_allowed else "P3通道❌"
        old = ev["old"]
        if old == "block":
            summary["old_block"] += 1
            if any_allowed:
                summary["p3_gain_from_block"] += 1
        elif old == "partial":
            summary["old_partial"] += 1
            if any_allowed:
                summary["p3_gain_from_partial"] += 1
        else:
            summary["old_pass"] += 1
        if not any_allowed and old != "pass":
            summary["still_block"].append(ev["id"])
        out_rows.append({"id": ev["id"], "date": ev["date"], "kind": ev["kind"], "wolf": ev["wolf"],
                         "wave_op": ev["op"], "has_base": ev["hb"], "t_universe": ev["tu"],
                         "old": old, "p3": p3, "allowed_intents": allowed_intents,
                         "decisions": [{k: v for k, v in d.items() if k not in ("reasons", "context")} for d in rows_dec]})
        print("%s %s %-8s wave=%s hb=%s tu=%s | old=%-7s -> %s" % (
              ev["id"], ev["date"], ev["kind"], ev["op"], int(ev["hb"]), int(ev["tu"]), old, p3), flush=True)
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "p3_tier_backtest.json")
    json.dump({"method": "P3 三仓档位 E01-E15 纯规则事件回测(v0)", "summary": summary, "events": out_rows},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", path, flush=True)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

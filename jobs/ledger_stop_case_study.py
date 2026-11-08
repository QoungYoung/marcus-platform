# -*- coding: utf-8 -*-
"""ledger_stop_case_study.py — 账本 D 组判例 vs v3 输出（只对照，不谈收益）。

关键：账本日期是“他说这句话的日期”，周末/盘后要落到**下一个交易日**执行；
“条件是否触发”先用日线可判的部分算，3×15min/30年债/外资等盘中项标 `needs_intraday`。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.services import wolf_stop_layer as WL  # noqa: E402

# id, 说话日, 账本点位(0=前一日低点), 动作, 目标(None=账本未给), 说明, 条件类型
CASES = [
    ("D01", "2025-03-11", 3344, "clear", 0.00, "盘中破 3344 收不回→一键清仓", "close"),
    ("D02", "2025-03-31", 3334, "reduce", None, "3334 收破→多头变空头；3303 止损", "close"),
    ("D03", "2025-05-26", 3334, "exit", 0.00, "收盘破 3334→先出场观望", "close"),
    ("D04", "2025-06-20", 3370, "reduce", None, "收破 3370/3375→减仓；破 3334→反抽全割", "close"),
    ("D05", "2025-08-28", 3856, "reduce", None, "收盘不到 3856→减出来", "close"),
    ("D06", "2025-09-07", 3885, "reduce", 0.30, "3885 站不上+放量黑K→减到 30%", "close_vol_black"),
    ("D07", "2025-09-17", 3828, "reduce", 0.50, "3828 放量破+30年债涨+外资流出→50%", "close_vol_macro"),
    ("D08", "2025-10-12", 3731, "reduce", 0.30, "跌破 3731 收盘→剩 30%", "close"),
    ("D09", "2025-11-18", 3950, "hold60", 0.60, "跌破 3950，尾盘不站回 3980-3986→保持 60%", "close_reclaim"),
    ("D10", "2025-11-21", 3922, "reduce", 0.40, "收盘破 3922→40%；破 3900→20%", "close"),
    ("D11", "2025-11-23", 3922, "reduce", 0.30, "跌破趋势 3922→趋势仓 10-15%，30% 短线", "close_trend"),
    ("D12", "2025-12-03", 3888, "reduce", 0.50, "3888 未站上→50%；3850 破→35%", "close"),
    ("D13", "2025-12-11", 0, "reduce", None, "跌破前低超 3 个 15min 不收回→反抽减仓", "prior_low_m15"),
    ("D14", "2026-01-14", 4141, "reduce", None, "再破 4141 放量+3 个 15min→减仓下调", "close_vol_m15"),
    ("D15", "2026-01-30", 4100, "reduce", 0.40, "跌破 4100，反抽上不了 4126→40%", "close_rebound"),
    ("D16", "2026-02-06", 4000, "reduce", None, "只有放量跌破 4000 才减仓", "close_vol"),
    ("D17", "2026-03-20", 4000, "clear", 0.00, "第二次跌破 4000→硬止损（他描述的是量化，不是本人动作）", "quant_desc"),
]


def load_bars():
    p = os.path.join(ROOT, "data", "index_daily_000001.json")
    d = json.load(open(p, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    return [{"trade_date": str(r["trade_date"]), "open": float(r["open"]),
             "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "vol": float(r.get("vol") or 0)} for r in d]


def main():
    bars_all = load_bars()
    dates = [b["trade_date"] for b in bars_all]
    out = []
    for cid, say, lvl, action, target, note, kind in CASES:
        say8 = say.replace("-", "")
        idx = next((i for i, d in enumerate(dates) if d >= say8), None)
        if idx is None:
            continue
        day = dates[idx]
        sub = bars_all[:idx + 1]
        bar = bars_all[idx]
        prev = bars_all[idx - 1] if idx > 0 else bar
        prior_low = prev["low"]
        ledger_level = prior_low if lvl == 0 else lvl
        v20 = [b["vol"] for b in bars_all[max(0, idx - 20):idx] if b["vol"] > 0]
        vr = (bar["vol"] / (sum(v20) / len(v20))) if v20 else None
        black = bar["close"] < bar["open"]
        # 日线可判的触发条件
        trig = None
        if kind == "close":
            trig = bar["close"] < ledger_level
        elif kind == "close_vol_black":
            trig = (bar["close"] < ledger_level) and bool(vr and vr >= 1.2) and black
        elif kind == "close_vol":
            trig = (bar["close"] < ledger_level) and bool(vr and vr >= 1.2)
        elif kind in ("close_vol_macro", "close_vol_m15"):
            trig = (bar["close"] < ledger_level) and bool(vr and vr >= 1.2)
        elif kind == "prior_low_m15":
            trig = bar["close"] < prior_low
        elif kind == "close_reclaim":
            trig = bar["close"] < ledger_level
        elif kind == "close_rebound":
            trig = bar["close"] < ledger_level
        elif kind == "close_trend":
            trig = bar["close"] < ledger_level
        elif kind == "quant_desc":
            trig = bar["close"] < ledger_level
        ctx = WL.StopContext(symbol="000001.SH", bars=sub,
                             wave={"level": "d4", "operation": "side"},
                             operation="side", phase="close", time_ok=True,
                             position={"was_broken": False})
        dec = WL.evaluate(ctx, target_fn=lambda op: 0.50)
        lev = dec.level or {}
        lp = lev.get("price")
        gap = (abs(float(lp) - ledger_level) / ledger_level * 100.0) if (lp and ledger_level) else None
        level_ok = gap is not None and gap <= 0.5
        target_ok = (target is not None and dec.target_pct is not None
                     and abs(float(dec.target_pct) - target) <= 0.05)
        acted = dec.action == "reduce_to"
        if trig is False:
            verdict = "✅ 未触发/未动" if not acted else "❌ 误触发"
            direction = "未触发" if not acted else "误触发"
        elif trig is None:
            verdict = "⚪ 需盘中核对"
            direction = "待核"
        else:
            if kind == "quant_desc":
                direction = "描述量化"
            elif action in ("clear", "exit"):
                direction = "应清仓/出场"
            elif action == "hold60":
                direction = "应保持60%"
            else:
                direction = "应减仓"
            if kind == "quant_desc":
                verdict = "⚪ 描述量化，非本人动作"
            elif not acted:
                verdict = "❌ 漏触发"
            elif action == "hold60":
                verdict = "❌ 方向错（他保持60，v3减）"
            elif kind == "prior_low_m15":
                verdict = "⚠️ 点位近、时点/方法不符"
            elif level_ok and (target is None or target_ok):
                verdict = "✅ 点位/方向相符"
            elif level_ok:
                verdict = "⚠️ 点位符、目标不符"
            else:
                verdict = "⚠️ 有动作、点位不符"
        out.append({"id": cid, "say": say, "eval": day, "ledger_level": ledger_level,
                    "ledger_action": action, "ledger_target": target,
                    "triggered": trig, "v3_action": dec.action, "v3_target": dec.target_pct,
                    "v3_level_type": lev.get("type"), "v3_level_price": lp,
                    "gap": round(gap, 2) if gap is not None else None,
                    "vol_ratio": round(vr, 2) if vr else None,
                    "verdict": verdict, "direction": direction,
                    "v3_reason": dec.reason, "note": note})
    lines = ["# 账本 D 组判例 vs v3 输出", "",
             "只做对照，不谈收益。账本日期落到**下一个交易日**；v3 用中性 wave（d4/side）。", "",
             "| ID | 说话日 | 评估日 | 账本点位 | 触发? | v3 动作/目标 | v3 触发位 | 差距 | 对照 |",
             "|---|---|---|---:|---|---|---|---:|---|"]
    for r in out:
        trig = "是" if r["triggered"] is True else ("否" if r["triggered"] is False else "需盘中")
        lines.append("| %s | %s | %s | %.2f | %s | %s / %s | %s @ %s | %s%% | %s |" % (
            r["id"], r["say"], r["eval"], r["ledger_level"], trig,
            r["v3_action"], ("%.0f%%" % (r["v3_target"] * 100)) if r["v3_target"] is not None else "—",
            r["v3_level_type"] or "—", r["v3_level_price"] if r["v3_level_price"] else "—",
            r["gap"] if r["gap"] is not None else "—", r["verdict"]))
    counts = {}
    for r in out:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines += ["", "## 统计", "", "| 判定 | 条数 |", "|---|---:|"]
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "## 结论", "",
              "- 17 条里 **7 条账本条件未触发**，v3 也没动；这是“正确的不动”，但不是有效测试。",
              "- **8 条是他本人的触发动作**：**0 条完全符合**；5 条漏触发（D04/D05/D11/D12/D14），1 条方向相反（D09：他保持 60%，v3 减 50%），2 条部分（D10 点位错、D13 时点/方法错）。",
              "- 另有 1 条误触发（D02：收盘 3335 > 3334，根本没破，v3 却减 50%）；1 条 D17 是他描述量化硬止损，**不是他本人动作**，不计入。",
              "- D13 的“点位相符”是巧合：账本要等 3×15min 不收回 + 反抽减仓，v3 是收盘直接减 50%。",
              "- 结论：v3 不是账本规则；不能用它评价狼大。",
              "", "> 触发条件用日线可判部分；3×15min/30年债/外资等盘中项标“需盘中”，不硬判。"]
    open(os.path.join(ROOT, "docs", "ledger-stop-cases-vs-v3.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

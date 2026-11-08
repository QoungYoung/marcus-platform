# -*- coding: utf-8 -*-
"""ledger_case_engine.py — 6 条指数 case 的逐条执行（2026-10-23）。

原则（教训）：
  · 点位用**他说的数字**，不用 PointEngine 猜；
  · 每条 case 单独定义触发/动作/目标，不抽通用规则；
  · 先做触发审计，再谈收益；
  · 账本没给目标的（R05）不替它编数字。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COST = 0.001

CASES = [
    {"id": "R04", "say": "2025-06-20", "eval": "20250620", "level": 3370,
     "start": 0.60, "target": 0.10, "action": "reduce", "action_type": "rebound_exit",
     "note": "收破 3370/3375；**反抽到 3370/3375 出掉**；减到 10% 底；破 3334 才反抽全割"},
    {"id": "R05", "say": "2025-08-28", "eval": "20250828", "level": 3856,
     "start": 1.00, "target": None, "action": "reduce", "action_type": "breakdown_reduce",
     "note": "收盘不到 3856，缩量；减出来（未给比例）"},
    {"id": "R09", "say": "2025-11-18", "eval": "20251118", "level": 3950,
     "start": 0.60, "target": 0.60, "action": "hold", "action_type": "hold",
     "note": "跌破 3950；尾盘不站回 3980-3986 → 保持 60%（不是减仓）"},
    {"id": "R10", "say": "2025-11-21", "eval": "20251121", "level": 3922,
     "start": 1.00, "target": 0.20, "action": "reduce", "action_type": "breakdown_reduce",
     "note": "收盘破 3922→40%；破 3900→20%；收 3834.89 → 20%"},
    {"id": "R11", "say": "2025-11-23", "eval": "20251124", "level": 3922,
     "start": 1.00, "target": 0.30, "action": "reduce", "action_type": "reclaim_failure",
     "note": "跌破趋势 3922 未站回 → 趋势仓 10-15%，30% 短线；按 30% cap 记"},
    {"id": "R12", "say": "2025-12-03", "eval": "20251203", "level": 3888,
     "start": 1.00, "target": 0.50, "action": "reduce", "action_type": "breakdown_reduce",
     "note": "3888 未站上→50%；3850 破才 35%；收 3878 → 50%"},
]


def load_bars():
    p = os.path.join(ROOT, "data", "index_daily_000001.json")
    d = json.load(open(p, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    return [{"date": str(r["trade_date"]), "open": float(r["open"]),
             "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "vol": float(r.get("vol") or 0)} for r in d]


def path_metrics(bars, start_i, n, exp):
    end = min(start_i + n, len(bars) - 1)
    c0 = bars[start_i]["close"]
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for i in range(start_i + 1, end + 1):
        r = bars[i]["close"] / bars[i - 1]["close"] - 1.0
        eq *= (1.0 + exp * r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    return {"ret": eq - 1.0, "mdd": mdd, "end": bars[end]["date"]}


def main():
    bars = load_bars()
    idx = {b["date"]: i for i, b in enumerate(bars)}
    rows = []
    for c in CASES:
        i = idx.get(c["eval"])
        if i is None or i + 1 >= len(bars):
            continue
        b = bars[i]
        triggered = b["close"] < c["level"]
        exec_i = i + 1
        row = dict(c)
        row.update({"close": b["close"], "triggered": triggered,
                    "exec": bars[exec_i]["date"], "windows": {}})
        if c["target"] is not None:
            for n in (5, 10, 20):
                hold = path_metrics(bars, exec_i, n, c["start"])
                led = path_metrics(bars, exec_i, n, c["target"])
                cost = COST * abs(c["start"] - c["target"])
                row["windows"][n] = {
                    "hold": hold["ret"], "ledger": led["ret"] - cost,
                    "excess": led["ret"] - cost - hold["ret"],
                    "hold_mdd": hold["mdd"], "ledger_mdd": led["mdd"],
                }
        rows.append(row)

    lines = ["# 6 条指数 case 逐条跑", "",
             "- 点位用他说的数字，不用 PointEngine 猜",
             "- 触发：评估日收盘 < 他给的点位",
             "- 执行：次日收盘；成本 0.1% × 调仓比例",
             "- R05 账本没给比例，不替它编数字，只做触发审计",
             "- R09 是“保持 60%”，不是减仓，单独标注", "",
             "## 1. 触发审计", "",
             "| ID | 说话日 | 评估日 | 他的点位 | 当日收盘 | 触发? | 动作/目标 | action_type | 备注 |",
             "|---|---|---|---:|---:|---|---:|---|---|"]
    for r in rows:
        tgt = ("%.0f%%" % (r["target"] * 100)) if r["target"] is not None else "未给"
        lines.append("| %s | %s | %s | %.2f | %.2f | %s | %s / %s | %s | %s |" % (
            r["id"], r["say"], r["eval"], r["level"], r["close"],
            "是" if r["triggered"] else "否", r["action"], tgt,
            r.get("action_type", ""), r["note"]))

    lines += ["", "## 2. 只做 breakdown_reduce 的 2 条：5/10/20 日", "",
              "| ID | 起始 | 目标 | 5日账本 | 5日hold | 5日超额 | 10日超额 | 20日账本 | 20日hold | 20日超额 | 20日MDD账本 | 20日MDD hold |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        if (not r["windows"] or r.get("action_type") != "breakdown_reduce"
                or r["target"] == r["start"]):
            continue
        w = r["windows"]
        def p(x): return "%+.2f%%" % (x * 100)
        lines.append("| %s | %.0f%% | %.0f%% | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], r["start"] * 100, r["target"] * 100,
            p(w[5]["ledger"]), p(w[5]["hold"]), p(w[5]["excess"]),
            p(w[10]["excess"]), p(w[20]["ledger"]), p(w[20]["hold"]), p(w[20]["excess"]),
            p(w[20]["ledger_mdd"]), p(w[20]["hold_mdd"])))

    lines += ["", "## 3. 汇总（仅 breakdown_reduce）", "",
              "| 窗口 | 账本平均 | hold 平均 | 平均超额 | 跑赢 hold |",
              "|---|---:|---:|---:|---:|"]
    for n in (5, 10, 20):
        vals = [r["windows"][n] for r in rows if r["windows"]
                and r.get("action_type") == "breakdown_reduce" and r["target"] != r["start"]]
        if not vals:
            continue
        avg_l = sum(v["ledger"] for v in vals) / len(vals)
        avg_h = sum(v["hold"] for v in vals) / len(vals)
        avg_e = sum(v["excess"] for v in vals) / len(vals)
        win = sum(1 for v in vals if v["excess"] > 0)
        lines.append("| %d日 | %+.2f%% | %+.2f%% | %+.2f%% | %d/%d |" % (
            n, avg_l * 100, avg_h * 100, avg_e * 100, win, len(vals)))

    lines += ["", "## 4. 读法", "",
              "- **6/6 当日都触发**：R04=`rebound_exit`，R05/R10/R12=`breakdown_reduce`，R11=`reclaim_failure`，R09=`hold`（不交易）。",
              "- 点位全部来自账本，不再出现“他 3922、引擎 3852”的错位。",
              "- R05 没给比例，只确认“该减”，不编目标。",
              "- 有目标的 4 条里，只有 R10/R12 属 `breakdown_reduce` 且可做同口径 P&L；R04 是反抽卖出、R11 是 reclaim_failure，不能混进“跌破减仓”的收益表。",
              "- 下一步：把 R12 的“3850→35%、3816 企稳→50%、3922-3930→70%”做成状态机；R10 的 3922/3900 两档已在目标里体现。",
              ]
    out = os.path.join(ROOT, "docs", "ledger-6-index-cases.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

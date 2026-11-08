# -*- coding: utf-8 -*-
"""ledger_event_study.py — 账本 D 组“已触发”判例的事件研究。

只取账本条件**确实触发**的日期（日线可判部分），按账本给的动作/目标算，不拿 v3 选日期。

口径：
  · 信号：判例评估日收盘；执行：**次日收盘**（D+1）
  · 成本：减仓 0.1% × 减仓比例；持有到窗口末不再计卖出成本（mark-to-market）
  · 窗口：5/10/20 个交易日
  · 账本目标：只用他明确给过的数字；未给目标的判例单独列，并注明“不纳入 headline”
  · 同一事件簇（2025-11）会重叠，样本 n 很小：只做判例对照，不做统计显著性
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# id, 说话日, 评估日, 目标, 动作说明, 是否账本明确目标
EVENTS = [
    ("D04", "2025-06-20", "20250620", 0.60, 0.10, "收破 3370→减仓；只剩 10% 底", True),
    ("D09", "2025-11-18", "20251118", 0.60, 0.60, "跌破 3950，尾盘不站回 3980-3986→保持 60%（**不是减仓动作**）", False),
    ("D10", "2025-11-21", "20251121", 1.00, 0.20, "收盘破 3922→40%；破 3900→20%", True),
    ("D11", "2025-11-23", "20251124", 1.00, 0.30, "跌破趋势 3922→30% 短线", True),
    ("D12", "2025-12-03", "20251203", 1.00, 0.50, "3888 未站上→50%", True),
    ("D17", "2026-03-20", "20260320", 1.00, 0.00, "第二次跌破 4000→硬止损（**他描述的是量化，不是他本人动作**）", False),
    # 以下账本只说“减/反抽减”，没给数字：单独列，不给 headline
    ("D05", "2025-08-28", "20250828", 1.00, None, "收盘不到 3856→减出来", False),
    ("D13", "2025-12-11", "20251211", 1.00, None, "跌破前低+3×15min→反抽减仓", False),
    ("D14", "2026-01-14", "20260114", 1.00, None, "再破 4141 放量+3×15min→减仓下调", False),
]
COST = 0.001
WINDOWS = (5, 10, 20)


def load_bars():
    p = os.path.join(ROOT, "data", "index_daily_000001.json")
    d = json.load(open(p, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    return [{"date": str(r["trade_date"]), "close": float(r["close"]),
             "high": float(r["high"]), "low": float(r["low"])} for r in d]


def path_metrics(bars, start, n, target):
    """start=执行日索引；返回窗口末收益与窗口内最大回撤（按 target 仓位）。"""
    end = min(start + n, len(bars) - 1)
    if end <= start:
        return None
    c0 = bars[start]["close"]
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for i in range(start + 1, end + 1):
        r = bars[i]["close"] / bars[i - 1]["close"] - 1.0
        eq *= (1.0 + target * r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    return {"ret": eq - 1.0, "mdd": mdd, "end": bars[end]["date"]}


def main():
    bars = load_bars()
    idx_of = {b["date"]: i for i, b in enumerate(bars)}
    out = []
    for cid, say, eval_day, start_exp, target, note, explicit in EVENTS:
        t0 = idx_of.get(eval_day)
        if t0 is None or t0 + 1 >= len(bars):
            continue
        exec_i = t0 + 1  # D+1 收盘执行
        row = {"id": cid, "say": say, "eval": eval_day, "exec": bars[exec_i]["date"],
               "start": start_exp, "target": target, "note": note, "explicit": explicit, "windows": {}}
        for n in WINDOWS:
            hold = path_metrics(bars, exec_i, n, start_exp)
            led = path_metrics(bars, exec_i, n, (target if target is not None else 0.5))
            if not hold or not led:
                continue
            # 交易成本：从 start_exp 调到 target
            cost = COST * abs(start_exp - (target if target is not None else 0.5))
            row["windows"][n] = {"hold_ret": hold["ret"], "hold_mdd": hold["mdd"],
                                 "ledger_ret": led["ret"] - cost,
                                 "ledger_mdd": led["mdd"],
                                 "excess": led["ret"] - cost - hold["ret"]}
        out.append(row)

    lines = ["# 账本 D 组事件研究（只取已触发判例）", "",
             "- 信号：判例评估日收盘；执行：**次日收盘**",
             "- 成本：减仓 0.1% × 减仓比例；窗口末按市值计，不再计卖出成本",
             "- 窗口：5/10/20 交易日",
             "- 账本目标只用他明确给过的数字；未给目标的判例单独列，**不纳入 headline**",
             "- 样本 n 很小（4 条明确减仓目标），且 2025-11 事件重叠；只做判例对照，不做统计显著性", "",
             "## 明确目标的判例", "",
             "| ID | 说话日 | 执行日 | 起始仓位 | 账本目标 | 5日账本 | 5日hold | 5日超额 | 10日账本 | 10日hold | 10日超额 | 20日账本 | 20日hold | 20日超额 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in out:
        if not r["explicit"]:
            continue
        w = r["windows"]
        def pct(x):
            return "%+.2f%%" % (x * 100) if x is not None else "—"
        lines.append("| %s | %s | %s | %.0f%% | %.0f%% | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], r["say"], r["exec"], r["start"] * 100, r["target"] * 100,
            pct(w.get(5, {}).get("ledger_ret")), pct(w.get(5, {}).get("hold_ret")), pct(w.get(5, {}).get("excess")),
            pct(w.get(10, {}).get("ledger_ret")), pct(w.get(10, {}).get("hold_ret")), pct(w.get(10, {}).get("excess")),
            pct(w.get(20, {}).get("ledger_ret")), pct(w.get(20, {}).get("hold_ret")), pct(w.get(20, {}).get("excess"))))
    lines += ["", "## 20 日回撤（明确减仓动作）", "",
              "| ID | 账本 MDD | hold MDD | 差（正=账本回撤更小） |",
              "|---|---:|---:|---:|"]
    for r in out:
        if not r["explicit"] or 20 not in r["windows"]:
            continue
        w = r["windows"][20]
        lines.append("| %s | %.2f%% | %.2f%% | %+.2fpp |" % (
            r["id"], w["ledger_mdd"] * 100, w["hold_mdd"] * 100,
            (w["ledger_mdd"] - w["hold_mdd"]) * 100))
    lines += ["", "## 未给目标的判例（不纳入 headline）", "",
              "| ID | 说话日 | 执行日 | 起始 | 账本说法 | 5日账本(按50%占位) | 5日hold | 10日超额 | 20日超额 |",
              "|---|---|---|---:|---|---:|---:|---:|---:|"]
    for r in out:
        if r["explicit"]:
            continue
        w = r["windows"]
        def pct(x):
            return "%+.2f%%" % (x * 100) if x is not None else "—"
        lines.append("| %s | %s | %s | %.0f%% | %s | %s | %s | %s | %s |" % (
            r["id"], r["say"], r["exec"], r["start"] * 100, r["note"],
            pct(w.get(5, {}).get("ledger_ret")), pct(w.get(5, {}).get("hold_ret")),
            pct(w.get(10, {}).get("excess")), pct(w.get(20, {}).get("excess"))))
    # aggregate explicit
    lines += ["", "## 汇总（4 条明确减仓动作；D09 保持60%、D17 描述量化，均不计入）", "",
              "| 窗口 | 账本平均 | hold 平均 | 平均超额 | 账本跑赢 hold 的条数 |",
              "|---|---:|---:|---:|---:|"]
    for n in WINDOWS:
        vals = [r["windows"][n] for r in out if r["explicit"] and r["target"] != r["start"] and n in r["windows"]]
        if not vals:
            continue
        avg_l = sum(v["ledger_ret"] for v in vals) / len(vals)
        avg_h = sum(v["hold_ret"] for v in vals) / len(vals)
        avg_e = sum(v["excess"] for v in vals) / len(vals)
        win = sum(1 for v in vals if v["excess"] > 0)
        lines.append("| %d日 | %+.2f%% | %+.2f%% | %+.2f%% | %d/%d |" % (
            n, avg_l * 100, avg_h * 100, avg_e * 100, win, len(vals)))
    lines += ["", "> 读法：超额 = 账本动作收益 − hold；正数表示这次减仓/清仓避开了下跌，负数表示卖飞。",
              "> 不能把 4 条当成统计结论；它只回答“这些判例里，他的动作事后看对不对”。"]
    out_path = os.path.join(ROOT, "docs", "ledger-event-study.md")
    open(out_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

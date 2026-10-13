# -*- coding: utf-8 -*-
"""规则级 A/B 对比器（§9.819，用户「去做方案1」✓）

用法：`.venv/bin/python scripts/ab_compare.py t42a t42b t42c`
输出（同窗口、单变量 ✓）：
  ① 逐臂：天数 / 触发数 / 各状态 / AI 决策计数 / 拦截图谱（按理由家族 ✓）
  ② 差异行：B/C 相对 A 的**变化量**（成交笔数、被拦数、各家族拦下数 ✓）
  ③ 账户曲线末值（`prod_*.json` 的 account ✓）
"""
import glob
import json
import os
import re
import sys
from collections import Counter


def load(tag):
    days, trig, agent, acc = [], [], Counter(), None
    for fp in sorted(glob.glob("data/_bt_%s/_summary/prod_*.json" % tag)):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        days.append(str(d.get("day")))
        for b in (d.get("bars") or []):
            a = b.get("agent")
            if isinstance(a, dict):
                for k in ("claim", "exec", "wait", "abandon", "update_condition", "wake_failed"):
                    agent[k] += int(a.get(k) or 0)
        for t in (d.get("triggers") or []):
            r = str(t.get("reason") or "")
            fam = ("无弹药/无底仓" if any(k in r for k in ("无弹药", "无底仓", "无券", "无可卖", "可为 0", "可卖 0", "裸空", "as-of")) else
                   "做T时段窗" if "做T时段窗" in r else
                   "B/C 时点闸" if "COND-TIMING" in r else
                   "底仓floor" if ("floor" in r or "收敛" in r) else
                   "加仓口径" if "加仓口径" in r else
                   "浪型/准入" if ("浪型" in r or "准入" in r or "档位" in r) else
                   "G8/涨停/其它" )
            trig.append({"day": d.get("day"), "event": str(t.get("event_type")), "status": str(t.get("status")),
                         "fam": fam, "reason": r})
        for p in (d.get("positions") or []):
            pass
        for a in (d.get("account") or []):
            acc = a
    return {"tag": tag, "days": days, "trig": trig, "agent": agent, "acc": acc}


def main():
    tags = sys.argv[1:] or ["t42a", "t42b", "t42c"]
    arms = {t: load(t) for t in tags}
    print("%-8s %6s %8s %8s %8s %8s %8s" % ("臂", "天数", "触发", "executed", "blocked", "cancelled", "ai_decided"))
    for t, a in arms.items():
        st = Counter(x["status"] for x in a["trig"])
        print("%-8s %6d %8d %8d %8d %8d %8d" % (t, len(a["days"]), len(a["trig"]), st.get("executed", 0),
                                                st.get("blocked", 0), st.get("cancelled", 0), st.get("ai_decided", 0)))
    print("\n=== 拦截图谱（按家族 ✓）===")
    fams = ["无弹药/无底仓", "做T时段窗", "B/C 时点闸", "底仓floor", "加仓口径", "浪型/准入", "G8/涨停/其它"]
    print("%-8s %s" % ("臂", "  ".join("%-12s" % f[:12] for f in fams)))
    for t, a in arms.items():
        c = Counter(x["fam"] for x in a["trig"] if x["status"] in ("blocked", "cancelled"))
        print("%-8s %s" % (t, "  ".join("%-12d" % c.get(f, 0) for f in fams)))
    if len(arms) > 1:
        base = arms[tags[0]]
        print("\n=== 相对 %s 的变化（同窗口、单变量 ✓）===" % tags[0])
        bst = Counter(x["status"] for x in base["trig"])
        bfam = Counter(x["fam"] for x in base["trig"] if x["status"] in ("blocked", "cancelled"))
        for t in tags[1:]:
            a = arms[t]
            st = Counter(x["status"] for x in a["trig"])
            cf = Counter(x["fam"] for x in a["trig"] if x["status"] in ("blocked", "cancelled"))
            print("  %-6s executed %+d ／ blocked %+d ／ cancelled %+d ／ AI-exec %+d" % (
                t, st.get("executed", 0) - bst.get("executed", 0), st.get("blocked", 0) - bst.get("blocked", 0),
                st.get("cancelled", 0) - bst.get("cancelled", 0),
                a["agent"]["exec"] - base["agent"]["exec"]))
            for f in fams:
                d = cf.get(f, 0) - bfam.get(f, 0)
                if d:
                    print("        %-12s %+d" % (f, d))
    print("\n=== 账户末值 ===")
    for t, a in arms.items():
        acc = a["acc"] or {}
        print("  %-6s 可用现金=%s 冻结=%s 初始=%s" % (t, acc.get("available_cash"), acc.get("frozen_cash"),
                                                     acc.get("initial_capital")))


if __name__ == "__main__":
    main()

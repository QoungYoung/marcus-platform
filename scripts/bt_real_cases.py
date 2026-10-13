# -*- coding: utf-8 -*-
"""②③ 真实触发用例 + 规则 oracle（§9.807）

数据源（真实回测产物 ✓，非合成）：
  data/_bt_<tag>/_summary/prod_*.json 的 `triggers[]`（一行一次真实触发 ✓）
    · 字段：id/symbol/event_type/status/trigger_price/quote_price/reason ✓
    · `bars[].agent` 给出逐 bar 的 AI 决策计数（claim/exec/wait/abandon ✓）

oracle（语料口径，逐条带出处 ✓）——把"系统拦/AI 决"的真实结果与语料对照：
  S1 无 T 仓弹药 / 可卖 0 / 裸空  ⇒ 语料「你们始终分不清做T仓位和底仓的区别」(2026-08-04)
     ⇒ T 腿只能动 T 仓 ⇒ **拦是对的 ✓**（不算背离）
  S2 as-of 持仓 0（无底仓）        ⇒ 同上 ✓
  S3 浪型 operation=t_only/defense/exit ⇒ 买类不放行 ✓（apps/main_line/wave_agent.py 口径）
  S4 条件单买入时点闸              ⇒ **待核 ✗**（未找到对应语料；疑自设 ⇒ 记 unclear）
  S5 桥不可达降级 / wake_failed    ⇒ **系统故障 ✗**（既非"该拦"也非"该放" ⇒ 记 broken）
  S6 底仓 floor 止盈腿收敛          ⇒ **待核 ✗**（疑自设，§4 未列 ⇒ 记 unclear）
  S7 其余（放行成交 / AI exec）    ⇒ 记 passthrough（需 AI 侧判据再评 ✓）
输出：docs/real-trigger-cases.json（前 60 条，含分类与 oracle 判定 ✓）+ 统计打印 ✓
"""
import glob
import json
import os
from collections import Counter, defaultdict

ROOTS = [("t41", "data/_bt_t41"), ("c0608", "data/_bt_c2610122052")]

RULES = {
    "no_ammo": {"verdict": "allow_block", "src": "2026-08-04「你们始终分不清 做T仓位和底仓的区别」⇒ T 腿只动 T 仓"},
    "asof_pos": {"verdict": "allow_block", "src": "同上（无底仓 ⇒ 做T 环起不来 ✓）"},
    "wave_gate": {"verdict": "allow_block", "src": "wave_agent.py operation 表：t_only/defense/exit ⇒ 买类拦 ✓"},
    "timing_gate": {"verdict": "unclear", "src": "❓未找到语料（条件单买入时点闸）⇒ 疑自设 ✗"},
    "bridge_down": {"verdict": "broken", "src": "系统故障（桥不可达 ⇒ AI 根本没被问到）✗"},
    "floor_converge": {"verdict": "unclear", "src": "❓底仓 floor 止盈收敛 —— §4 未列，待核 ✗"},
    "other": {"verdict": "passthrough", "src": "需 AI 侧判据再评"},
}


def classify(reason: str) -> str:
    r = str(reason or "")
    if ("可为 0" in r) or ("无 T 仓" in r) or ("裸空" in r) or ("无T仓" in r):
        return "no_ammo"
    if "as-of" in r:
        return "asof_pos"
    if "浪型" in r:
        return "wave_gate"
    if "COND-TIMING" in r or "时点闸" in r:
        return "timing_gate"
    if "桥不可达" in r or "wake" in r.lower():
        return "bridge_down"
    if "floor" in r or "收敛" in r:
        return "floor_converge"
    return "other"


def main():
    cases = []
    per_class = Counter()
    per_class_status = defaultdict(Counter)
    agent_tot = Counter()
    for tag, root in ROOTS:
        for p in sorted(glob.glob(os.path.join(root, "_summary", "prod_*.json"))):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            for b in (d.get("bars") or []):
                a = b.get("agent")
                if isinstance(a, dict):
                    for k in ("claim", "exec", "wait", "abandon", "update_condition", "wake_failed"):
                        agent_tot[k] += int(a.get(k) or 0)
            for t in (d.get("triggers") or []):
                cls = classify(t.get("reason"))
                per_class[cls] += 1
                per_class_status[cls][str(t.get("status"))] += 1
                if len(cases) < 60:
                    cases.append({"arm": tag, "day": d.get("day"), "symbol": t.get("symbol"),
                                  "event_type": t.get("event_type"), "status": t.get("status"),
                                  "reason": str(t.get("reason") or "")[:150],
                                  "cls": cls, "oracle": RULES[cls]["verdict"], "src": RULES[cls]["src"]})
    os.makedirs("docs", exist_ok=True)
    json.dump(cases, open("docs/real-trigger-cases.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("用例文件 = docs/real-trigger-cases.json（%d 条真实触发 ✓）" % len(cases))
    print("全量触发分类（两臂合计）:")
    n = sum(per_class.values())
    for k, v in per_class.most_common():
        print("  %-16s %5d（%4.1f%%）  oracle=%-12s status=%s"
              % (k, v, 100.0 * v / max(1, n), RULES[k]["verdict"], dict(per_class_status[k])))
    print("AI 决策合计（bars[].agent）:", json.dumps(dict(agent_tot), ensure_ascii=False))
    ok = per_class.get("no_ammo", 0) + per_class.get("asof_pos", 0) + per_class.get("wave_gate", 0)
    print("⇒ 语料支持的正确拦截 = %d/%d = %.0f%%；待核(unclear) = %d；系统故障 = %d；其余放行流 = %d"
          % (ok, n, 100.0 * ok / max(1, n), per_class.get("timing_gate", 0) + per_class.get("floor_converge", 0),
             per_class.get("bridge_down", 0), per_class.get("other", 0)))


if __name__ == "__main__":
    main()

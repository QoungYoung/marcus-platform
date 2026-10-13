# -*- coding: utf-8 -*-
"""条件-动作对拍器（§9.816，用户选「方案②」✓）

口径（按用户 2026-10-13 的指正 ✓：规则是**组合条件**，不做单因子切片 ✗）：
  · **腿型规则**（254/253/埋伏/趋势突破…）：**触发本身 = 条件成立** ✓（它们的入场表达式就是条件合取式 ✓）
    ⇒ 看"条件成立后系统做了什么" ✓：exec（命中 ✓）／blocked·cancelled（被拦 ✗）／…
  · **闸门规则**（B/C 时点闸、底仓 floor、G7…）：拦截理由就是它们的动作 ✓
    ⇒ 对每条拦截理由判**语料归属**：有出处 ✓ / 无出处 ✗（逐条查表 ✓）
  · AI 侧（做T唤醒）单独一表：`bars[].agent` 的 exec/wait/abandon ✓（不与被拦混算 ✓）

输出：规则 × 结果 矩阵 + 拦截合规率 + 逐条出处 ✓（真实数据 ✓ 离线 ✓ 可复跑 ✓）
"""
import glob
import json
from collections import Counter, defaultdict

ROOTS = ("data/_bt_t41", "data/_bt_c2610122052")

# 规则 → 覆盖的 event_type（腿型规则：触发=条件成立 ✓）
RULES = {
    "254 低吸腿(挂昨低∧缩量)": ("custom_prevlow",),
    "253 急杀腿(大盘 m5_dump)": ("custom_m5dump",),
    "埋伏腿(ambush)": ("wolf_ambush_buy",),
    "趋势突破(trend_break_buy)": ("trend_break_buy",),
    "做T低吸(low_buy/wolf_zheng_t_buy)": ("low_buy", "wolf_zheng_t_buy"),
    "防守减仓(defensive_t_reduce)": ("wolf_defensive_t_reduce",),
    "高抛兑现(high_sell 族)": ("high_sell", "wolf_dao_t_sell", "wolf_profit_take_sell",
                          "wolf_fib_target_sell", "wolf_board_half_sell", "wolf_confirm_sell"),
}
# 拦截理由 → 语料归属（逐条查表 ✓；unclear=找不到出处 ✗；broken=系统故障 ✗）
REASON_MAP = [
    # ① 无 T 仓/无底仓族（做T 环起不来 ✓ 语料：他反复讲"做T仓位和底仓的区别"）
    ("无弹药", "allow", "2026-08-04「你们始终分不清 做T仓位和底仓的区别」⇒ T 腿只动 T 仓 ✓"),
    ("无底仓", "allow", "同上 ✓（无底仓 ⇒ 做T 环起不来 ✓）"),
    ("无券", "allow", "同上 ✓"),
    ("无可卖", "allow", "同上 ✓"),
    ("需可卖底仓", "allow", "同上 ✓"),
    ("可为 0", "allow", "同上 ✓"),
    ("可卖 0", "allow", "同上 ✓"),
    ("裸空", "allow", "同上 ✓"),
    ("as-of", "allow", "as-of 快照显示持仓/可卖不足 ⇒ 同上 ✓"),
    # ★ 2026-10-13 复核更正：底仓 floor = **部分有据**（原则有 ✓ / 比例与"绝对不可减"是代理 ✗）
    ("底仓floor", "partial", "「底仓不动」有原话 ✓（2026-08-04「做T仓位和底仓的区别」）；"
                           "**50% 比例**与「底仓不可减」是**我们的代理** ✗（台账自陈）"
                           "（已有亏损豁免开关 WOLF_MA5_EXIT / WOLF_BASE_FLOOR_LOSS_EXEMPT ✓ 部分修正）"),
    ("floor", "partial", "同上 ✓/✗"),
    ("收敛", "partial", "同上 ✓/✗"),
    # ② 浪型/时点/时段/上影线（都有出处 ✓）
    ("浪型", "allow", "wave_agent operation 表（t_only/defense/exit ⇒ 买类拦 ✓）"),
    ("COND-TIMING", "allow", "2025-06-05「急杀可以买，缓跌不买」✓（B 闸=不在下跌中买 ✓）"),
    ("做T时段窗", "allow", "playbook:287「只做上午 9:45–10:00、14:00–14:30」✓"),
    ("G8", "allow", "2025-07-17「看见出上影线 立马停止做T」✓"),
    ("放量破前低", "allow", "C2″：放量∧破前低∧破VWAP=恐慌急杀（台账 I/J 节实测拦对了 ✓）"),
    # ③ 交易所/额度/去重（事实或用户拍板 ✓）
    ("涨停", "allow", "涨跌停不可成交（交易所事实 ✓）"),
    ("埋伏腿同日去重", "allow", "既定（真在拦 ✓）"),
    ("埋伏同时持仓", "allow", "§9.197 用户拍板 ≤15 ✓"),
    ("本轮已", "allow", "G6 日额度（用户拍板口径 ✓）"),
    ("买入1笔、卖出2笔", "allow", "G6 日额度 ✓"),
    # ④ 待核（找不到语料 ✗）
    # ★ 复核更正：加仓口径 **有据 ✓**（reason 里自带原话+日期 ✓，先前是我的归类器没解析 ✗）
    ("加仓口径", "allow", "狼大 2026-04-10「怕忍不住买回来…找低位的线挂进去 挂远一点」✓ / "
                        "2025-04-03「冲上去一定不能追」✓ / 2026-06-15「第一根加一半+第二根尾盘打满，之后不再加」✓"),
    ("同轮累计", "unclear", "❓未找到语料（待核 ✗）"),
    ("下去不补", "unclear", "❓未找到语料（待核 ✗）"),
    ("买腿非低吸", "unclear", "❓未找到语料（待核 ✗）"),
    ("低吸价未到位", "unclear", "❓未找到语料（待核 ✗）"),
    # ⑤ 系统故障
    ("桥不可达", "broken", "系统故障（AI 根本没被问到）✗"),
]


def _fam(r):
    """归一化到"家族"级 ✓（去掉代码/数字/价格 ⇒ 同类合并 ✓）"""
    import re as _re
    x = _re.sub(r"[A-Z]{2}\d{6}|\d+\.?\d*", "X", str(r or ""))
    x = _re.sub(r"[：:，,。；; ]+", " ", x).strip()
    return x[:34] or "(空)"


def classify(reason):
    r = str(reason or "")
    for pat, verdict, src in REASON_MAP:
        if pat in r:
            return verdict, pat, src
    return "unclear", _fam(r), "❓未找到对应语料（待核 ✗）"


def main():
    trig = []
    agent = Counter()
    for root in ROOTS:
        for fp in glob.glob(root + "/_summary/prod_*.json"):
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            for b in (d.get("bars") or []):
                a = b.get("agent")
                if isinstance(a, dict):
                    for k in ("claim", "exec", "wait", "abandon", "update_condition", "wake_failed"):
                        agent[k] += int(a.get(k) or 0)
            for t in (d.get("triggers") or []):
                t["_day"] = d.get("day")
                trig.append(t)
    print("真实触发 = %d 条（t41 + c 两臂 ✓）" % len(trig))
    print("\n=== ① 腿型规则 × 结果（触发=条件成立 ✓）===")
    print("%-34s %6s %8s %8s %8s %8s" % ("规则", "触发", "executed", "blocked", "cancelled", "ai_decided"))
    used = set()
    for name, kinds in RULES.items():
        rows = [t for t in trig if str(t.get("event_type")) in kinds]
        used |= set(kinds)
        if not rows:
            continue
        st = Counter(str(t.get("status")) for t in rows)
        print("%-34s %6d %8d %8d %8d %8d" % (name, len(rows), st.get("executed", 0), st.get("blocked", 0),
                                             st.get("cancelled", 0), st.get("ai_decided", 0)))
    other = [t for t in trig if str(t.get("event_type")) not in used]
    if other:
        st = Counter(str(t.get("status")) for t in other)
        print("%-34s %6d %8d %8d %8d %8d" % ("(其它类型)", len(other), st.get("executed", 0),
                                             st.get("blocked", 0), st.get("cancelled", 0), st.get("ai_decided", 0)))
    print("\n=== ② 拦截理由的语料归属（组合条件规则 ✓ 不做单因子切片 ✓）===")
    agg = defaultdict(lambda: {"n": 0, "src": "", "verdict": ""})
    for t in trig:
        if str(t.get("status")) not in ("blocked", "cancelled"):
            continue
        v, pat, src = classify(t.get("reason"))
        a = agg[(v, pat)]
        a["n"] += 1; a["src"] = src; a["verdict"] = v
    tot = sum(a["n"] for a in agg.values())
    print("%-9s %-22s %6s  %s" % ("归属", "理由", "条数", "语料 / 说明"))
    for (v, pat), a in sorted(agg.items(), key=lambda x: -x[1]["n"]):
        print("%-9s %-22s %6d  %s" % (v, pat, a["n"], a["src"][:78]))
    part = sum(a["n"] for (v, _p), a in agg.items() if v == "partial")
    ok = sum(a["n"] for (v, _p), a in agg.items() if v == "allow")
    unc = sum(a["n"] for (v, _p), a in agg.items() if v == "unclear")
    brk = sum(a["n"] for (v, _p), a in agg.items() if v == "broken")
    print("\n⇒ 拦截合计 %d：**有语料依据 %d（%.0f%%）** ✓ ／ **部分有据(原则✓/比例✗) %d（%.0f%%）** ✗ ／ "
          "待核 %d（%.0f%%）✗ ／ 系统故障 %d（%.0f%%）✗"
          % (tot, ok, 100.0 * ok / max(1, tot), part, 100.0 * part / max(1, tot),
             unc, 100.0 * unc / max(1, tot), brk, 100.0 * brk / max(1, tot)))
    print("\n=== ③ AI 侧（做T唤醒决策，单独计 ✓ 不与系统拦截混算 ✗）===")
    n = sum(agent.values())
    print("  %s ⇒ exec %.0f%% / wait+abandon %.0f%% / wake_failed %.0f%%"
          % (json.dumps(dict(agent), ensure_ascii=False), 100.0 * agent["exec"] / max(1, agent["claim"]),
             100.0 * (agent["wait"] + agent["abandon"]) / max(1, agent["claim"]),
             100.0 * agent["wake_failed"] / max(1, agent["claim"])))
    print("\n注（口径边界 ✗）：'条件不成立却动作'那一格**无法观测** ✗（记录里只有触发后的事 ✓）；"
          "AI 的 exec/wait 与被拦是两条链 ✓ 分开看 ✓")


if __name__ == "__main__":
    main()

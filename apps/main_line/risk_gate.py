# -*- coding: utf-8 -*-
"""
risk_gate.py — P2 风控规则 v1（基于 docs/p2-risk-wolf-logic.md R-R1~R-R4）
输入: 候选/持仓标的 + 事件标志 + 两融分位 → 决策 {allow / block / reduce / review}
规则:
  R-R1 硬黑名单: ST/退市风险/立案/监管处罚/重组终止/业绩暴雷(已披露) → block
  R-R2 财报窗口: 未披露财报 + 高位/高预期 → review(等业绩落地), 不硬禁
  R-R3 查杠杆: 两融分位≥0.8 且 指数高位破位信号 → reduce(系统性减仓), macro_risk=True
  R-R4 拥挤回避: 由 rotation_universe 承接(本模块不重复), 预留 crowded 参数
用法: python apps/main_line/test_risk_gate.py
"""
import calendar, datetime

HARD_FLAGS = {"st", "delist_risk", "立案", "监管处罚", "重组终止", "业绩暴雷", "退市风险"}
# A股财报披露月末窗口(±容忍日)
EARNINGS_MONTH_END = {1: 31, 3: 31, 4: 30, 6: 30, 7: 31, 8: 31, 10: 31}  # 关键窗口 4/7/8/10

def _today():
    return datetime.date.today()

def in_earnings_window(day=None):
    """财报密集窗口: 4月(年报+一季), 7月中-8月底(中报), 10月(三季)"""
    day = day or _today()
    m = day.month
    return m in (4, 7, 8, 10)

def decide(cand, margin_pct=None, top_signal=False, day=None):
    """
    cand: {symbol, is_st?, flags:[...], earnings_bad?, earnings_clear?, high_position?, crowded?}
    margin_pct: 两融余额历史分位 0~1 (None=未知)
    top_signal: 指数高位 + 破位/缩量滞涨(查杠杆系统性信号)
    返回 {decision, risk_flags, macro_risk, reason}
    """
    flags = set(cand.get("flags") or [])
    if cand.get("is_st"):
        flags.add("st")
    reasons = []
    macro = False
    # R-R1 硬黑名单
    hit = flags & HARD_FLAGS
    if hit:
        return {"decision": "block", "risk_flags": sorted(hit), "macro_risk": False,
                "reason": "硬黑名单: " + "、".join(sorted(hit))}
    # 业绩暴雷另一字段
    if cand.get("earnings_bad"):
        return {"decision": "block", "risk_flags": ["业绩暴雷"], "macro_risk": False,
                "reason": "业绩暴雷/预亏: 不建仓"}
    # R-R3 查杠杆系统性
    if margin_pct is not None and margin_pct >= 0.8 and top_signal:
        macro = True
        return {"decision": "reduce", "risk_flags": ["两融高位+查杠杆"], "macro_risk": True,
                "reason": "两融分位%.0f%%且指数高位破位→系统性减仓(R3)" % (margin_pct * 100)}
    # R-R2 财报窗口
    if in_earnings_window(day) and not cand.get("earnings_clear"):
        if cand.get("high_position"):
            return {"decision": "review", "risk_flags": ["财报窗口-高位未落地"], "macro_risk": macro,
                    "reason": "财报窗口+高位: 等业绩落地再决策(R2)"}
        reasons.append("财报窗口内建议等业绩落地")
    # R-R4 拥挤(可选输入)
    if cand.get("crowded"):
        return {"decision": "review", "risk_flags": ["公募拥挤-拥挤无空间"], "macro_risk": macro,
                "reason": "公募拥挤无空间: 回避新建(rotation_universe)"}
    return {"decision": "allow", "risk_flags": sorted(flags), "macro_risk": macro,
            "reason": "无硬性风险: " + ("、".join(sorted(flags)) or "通过")}

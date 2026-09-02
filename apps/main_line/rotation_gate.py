# -*- coding: utf-8 -*-
"""
rotation_gate.py — P2 轮动 gate v2（基于 docs/p2-rotation-validation-analysis.md 规律①-③ + B3 补丁）
输入(点内): wave_op + 持仓A状态 + 候选B状态 + 主线吸金/轮动健康度
输出: verdict ∈ {mainline_rotation, switch_low, defensive_reduce, sell_guard,
                 defense_mainline_rotation, block, manual_review}

规则骨架(2026-09-02 语料+23/23验证定稿):
- build + 主线明牌吸金   → 只允许主线内细分轮动/补涨；禁切出主线(A2/A3/A4/D5)
- build + 持仓A 高位资金流出≥2日/结构破位 → 允许撤A; B须rel-low+资金流入+技术底线(B4/B5)
- t_only/side + 轮动健康 → 允许防御性切低(rel-low/ETF)或主线内调仓(C1/C2/A1)
- t_only/side + 抽血/无主线快速轮动 → 禁切出，只防守/降个股转ETF(D4/D6)
- defense/exit → 禁新开/禁切出主线；允许主线内"未出货链"资金调仓(B3)
- 龙头死/持仓高位破位 → sell_guard(撤A不切低)(B1)
"""
def top_warn(a):
    """持仓A是否触发"高位+资金流出/结构破位"预警(B4/B5/C3技术信号)"""
    if not a: return False
    pos = a.get("position"); fund = a.get("fund") or {}
    st = a.get("structure") or "flat"
    high_flow_out = pos in ("HIGH",) and fund.get("dir") == "out" and (fund.get("conv") or 0) >= 2
    struct_break = st in ("双头M顶", "新高回落", "破位C杀")
    return bool(high_flow_out or struct_break)

def b_ok_switch(b):
    """候选B是否满足切低底线: rel-low(相对主线) + 资金流入 + 无2孕线/未放量破前日低"""
    if not b: return False
    return bool(b.get("rel") == "low" and (b.get("fund") or {}).get("dir") == "in" and b.get("struct_ok", True))

def decide(wave_op, a=None, b=None, mainline_sucking=False, inside_mainline=False, not_distributed=False, rotation_healthy=True):
    """wave_op: build/t_only/side/defense/exit; a/b: dict; 返回 {verdict, reason}"""
    if wave_op in ("defense", "exit"):
        if inside_mainline and not_distributed:
            return {"verdict": "defense_mainline_rotation",
                    "reason": "defense/exit 期允许主线内'已出货/破位链→未出货链'资金调仓(B3)"}
        return {"verdict": "block", "reason": "defense/exit: 禁新开/禁切出主线, 离场防守(D6/B3之外)"}
    if wave_op in ("t_only", "side"):
        if mainline_sucking or not rotation_healthy:
            return {"verdict": "block", "reason": "非build但主线明牌吸金/抽血或无主线快速轮动: 禁切出, 只防守(D4/D5/D6)"}
        if b_ok_switch(b):
            return {"verdict": "switch_low", "reason": "t_only/side 健康+候选rel-low/资金流入: 防御性切低(C1/C3)"}
        aw = top_warn(a)
        if aw:
            return {"verdict": "sell_guard", "reason": "持仓A高位破位, 无合格候选: 撤A不切低(B1)"}
        if inside_mainline:
            return {"verdict": "mainline_rotation", "reason": "t_only/side 主线内筛票/轮动调仓(A1/A5)"}
        return {"verdict": "defensive_reduce", "reason": "t_only/side 健康: 可降个股转ETF/埋伏rel-low(C4/C5/C6/A6)"}
    if wave_op == "build":
        aw = top_warn(a)
        if aw:
            if b_ok_switch(b):
                return {"verdict": "switch_low", "reason": "build 内持仓高位流出/破位→可切rel-low(B4/B5)"}
            return {"verdict": "sell_guard", "reason": "build 内持仓高位破位但候选不合格: 撤A" }
        if mainline_sucking:
            if inside_mainline:
                return {"verdict": "mainline_rotation", "reason": "build 明牌吸金: 只做主线内细分轮动(A2/A3)"}
            return {"verdict": "block", "reason": "build 明牌吸金: 禁切出主线(A4/D5)"}
        if inside_mainline:
            return {"verdict": "mainline_rotation", "reason": "build 主线内细分轮动/补涨(A2/A3/B2)"}
        if b_ok_switch(b):
            return {"verdict": "switch_low", "reason": "build 健康但有rel-low资金流入候选: 可低吸埋伏"}
        return {"verdict": "manual_review", "reason": "build 无主线吸金也无合格候选: 需人工看盘"}
    return {"verdict": "block", "reason": "unknown wave_op: %s" % wave_op}

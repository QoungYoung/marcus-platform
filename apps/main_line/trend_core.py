# -*- coding: utf-8 -*-
"""trend_core.py — 趋势底仓 v2 纯状态机（D1）

输入：浪型 operation、四前提、黄白线 spread5、当前 core 仓位、破位标志。
输出：core_state / core_action / total_target / core_target / t_target / cash。

口径（2026-10-20 用户拍板）：
  · 总仓位档：build 75 / side 50 / t_only 50 / defense 30 / exit 0
  · core 目标：build 50 / side 30 / t_only 30（只维持、不新建）/ defense 15 / exit 0
  · t_target = total_target - core_target；cash = 100 - total_target
  · 四前提：主线 confirmed、浪型 build/side、黄白线 spread5>-0.2、个股 leader
  · core 穿越 3-2/4-4；但 platform_break/double_top/time_stop/gap_unfilled/logic_break 仍减
  · T 仓不能卖 core；core 只由本状态机减
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

TOTAL_TARGET = {"build": 75.0, "side": 50.0, "t_only": 50.0, "defense": 30.0, "exit": 0.0}
CORE_TARGET = {"build": 50.0, "side": 30.0, "t_only": 30.0, "defense": 15.0, "exit": 0.0}
VALID_OPS = tuple(TOTAL_TARGET)


@dataclass(frozen=True)
class CoreInputs:
    operation: str
    mainline_confirmed: bool = False
    leader_ok: bool = False
    spread5: Optional[float] = None          # 黄线-白线 5 日 spread（pp）
    core_exists: bool = False
    core_pct: float = 0.0                    # 当前 core 占总权益 %
    break_flags: Tuple[str, ...] = ()        # platform_break/double_top/time_stop/gap_unfilled/logic_break


@dataclass(frozen=True)
class CoreDecision:
    state: str
    action: str
    total_target_pct: float
    core_target_pct: float
    t_target_pct: float
    cash_pct: float
    reason: str
    flags: Tuple[str, ...] = field(default_factory=tuple)


def _regime_ok(spread5: Optional[float]) -> bool:
    # 缺数据 fail-open；有数据时白线持续强（<= -0.2）→ 不允许新建 core
    return spread5 is None or float(spread5) > -0.2


def decide(inp: CoreInputs) -> CoreDecision:
    """纯函数：给定状态，返回 core 目标与动作。"""
    op = str(inp.operation or "").strip().lower()
    if op not in VALID_OPS:
        op = "exit"
    total = float(TOTAL_TARGET[op])
    core_t = float(CORE_TARGET[op])
    flags = tuple(str(x) for x in (inp.break_flags or ()))
    core_pct = max(0.0, min(100.0, float(inp.core_pct or 0.0)))
    core_exists = bool(inp.core_exists or core_pct > 0)
    regime = _regime_ok(inp.spread5)
    premises = bool(inp.mainline_confirmed and inp.leader_ok and regime and op in ("build", "side"))

    def out(state: str, action: str, target: float, reason: str) -> CoreDecision:
        target = max(0.0, min(total if total > 0 else 100.0, float(target)))
        t = max(0.0, total - target)
        cash = max(0.0, 100.0 - total)
        return CoreDecision(state=state, action=action, total_target_pct=total,
                            core_target_pct=round(target, 1), t_target_pct=round(t, 1),
                            cash_pct=round(cash, 1), reason=reason, flags=flags)

    # 1) 硬退出
    if op == "exit" or "logic_break" in flags:
        return out("EXIT", "exit", 0.0, "operation=exit 或 logic_break")

    # 2) 结构破位：先减半 core
    if "platform_break" in flags or "double_top" in flags:
        target = core_pct * 0.5 if core_exists else 0.0
        return out("TRIM", "trim", target, "platform_break/double_top → core 减半")

    # 3) 时间/缺口：减 1/3
    if "time_stop" in flags or "gap_unfilled" in flags:
        target = core_pct * 2.0 / 3.0 if core_exists else 0.0
        return out("TRIM", "trim", target, "time_stop/gap_unfilled → core 减 1/3")

    # 4) 风险档：core 压到 defense 目标
    if op == "defense":
        target = min(core_pct, core_t) if core_exists else 0.0
        state = "TRIM" if core_exists and core_pct > core_t + 0.1 else ("HOLD" if core_exists else "OFF")
        return out(state, "trim" if state == "TRIM" else ("hold" if state == "HOLD" else ""),
                   target, "operation=defense → core 降到 defense 档")

    # 5) t_only：只维持已有 core，不新建、不加
    if op == "t_only":
        if not core_exists:
            return out("OFF", "", 0.0, "operation=t_only，无 core，不新建")
        if core_pct > core_t:
            return out("TRIM", "trim", core_t, "operation=t_only，core 超上限 → 压回")
        return out("HOLD", "hold", core_pct, "operation=t_only，维持 core，只做 T")

    # 6) 四前提不全
    if not premises:
        reasons = []
        if not inp.mainline_confirmed: reasons.append("mainline 未确认")
        if not inp.leader_ok: reasons.append("非 leader")
        if not regime: reasons.append("黄白线白线强")
        if op not in ("build", "side"): reasons.append("operation=%s" % op)
        if core_exists:
            # 前提坏了：core 减半，不一次性清；等结构/浪型确认再清
            return out("TRIM", "trim", core_pct * 0.5, "四前提不全：" + "、".join(reasons))
        return out("OFF", "", 0.0, "四前提不全：" + "、".join(reasons))

    # 7) 四前提全过
    if not core_exists:
        return out("BUILD", "build", core_t, "四前提全过 → 建 core")
    if core_pct < core_t - 0.5:
        return out("HOLD", "add", core_t, "core 低于目标 → 加")
    if core_pct > core_t + 0.5:
        return out("TRIM", "trim", core_t, "core 高于目标 → 减")
    return out("HOLD", "hold", core_pct, "core 达到目标 → 持有")

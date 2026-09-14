# -*- coding: utf-8 -*-
"""roundtrip_priority.py — A1（2026-09-14）：「+3% 目标优先」+「破黄线需确认」。

背景（真 m5 验收结论，docs/exit-rules-m5-report.md）：
  · 用标的自己的分时均价线（黄线）判"破线"，**无论全天还是只在他两个做 T 窗口内，33/33 条腿都会破**
    → 破线是持仓期间几乎必然发生的状态，**不是择时信号**；
  · 生产 t_monitor._check_roundtrip_sell 原先把 vwap_break 当**优先离场**条件 →
    "低吸后反弹 +3% 卖回"大多被提前触发（V1 +1.29% → V3c −0.06%）。

狼大原话（两条，正好对应"目标"与"破线"）:
  · 2026-08-04 10:48「在这个半小时内有个绝对不能破的点 就是日均线那条黄线，**一旦突发跌破直接走**。
    **如果没跌破就找这半小时的高点**。」
  · 2026-09-02「2 点到了 力度不够…今天目标 3%-4% 实际 2% 结束今天半导体做 T 操作」；
    2026-08-13 楼275/280「至少能有吃 **3-5 个点**的幅度吧」。

A4（2026-09-14，用户"改成跟狼大一致"）—— **做 T 时间窗 + 2 点决断**：
  · 2025-04-15 成文流程条件 2：「**当日只做上午 9:45–10:00、下午 14:00–14:30 这两个时间段**」
    （docs/wolf-playbook.md §条件2）→ "目标兑现"只在窗口内执行，窗口外不因达标而卖（让它跑）；
  · 2026-09-02 14:03「**2 点到了 力度不够 我先把早上博弈的先T了 今天目标3%-4% 实际2% 结束今天半导体做T操作**」
    → 到 14:00 仍未达标也**T 掉收工**（"挣不到就亏个手续费出"，同 docs/wolf-daily-log-nga.md 做T行）；
  · 2026-08-04 10:48「一旦**突发**跌破直接走。**如果没跌破就找这半小时的高点**」
    → 窗口内达标即走 / 窗口内不破线就等窗口高点，二者统一为"窗口内兑现"。
  · 保护不变：确认破黄线任何时候都可走（A1）。
  开关：`WOLF_RT_WINDOW=0` 关掉窗口语义（= A1 原行为）；`WOLF_RT_WINDOWS`、`WOLF_RT_FORCE_HM`、
  `WOLF_RT_TIMEOUT_TOL`（到点决断允许的浮亏容忍，默认 0.5% = "亏个手续费"）。

A1 口径:
  1. **目标优先**：现价 ≥ 低吸均价×(1+兑现幅度) → 直接卖（对应"没跌破就找这半小时的高点"）；
  2. **破线要确认**（对应"**突发**跌破"）：满足其一即算确认 ——
     a. **幅度**：现价 ≤ 黄线×(1 − WOLF_VWAP_BREAK_PCT，默认 0.5%)（"不是贴着线蹭一下"）；
     b. **持续**：连续 WOLF_VWAP_BREAK_ROUNDS（默认 2 轮，TMonitor 一轮 30s）现价都在黄线下方；
  3. 回退：`WOLF_RT_PRIORITY=vwap_first` 回旧行为（破线优先）；把 PCT=0 且 ROUNDS=1 即"一破就走"。

纯函数（可单测）：roundtrip_decision(cur, buy_avg, avg, up, streak, hm, ...) → (action, why, new_streak)
  action ∈ {"sell_target", "sell_timebox", "sell_vwap", "wait"}。
"""
from __future__ import annotations

import os
from typing import Optional, Tuple


def _f(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)))
    except (TypeError, ValueError):
        return dflt


def _i(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt))))
    except (TypeError, ValueError):
        return dflt


def priority() -> str:
    """target_first（默认，A1）｜ vwap_first（旧行为，回退用）。"""
    return (os.getenv("WOLF_RT_PRIORITY", "target_first") or "target_first").strip().lower()


def vwap_break_pct() -> float:
    """"突发跌破"的幅度确认阈值（默认 0.5%）。0 = 不做幅度确认。"""
    try:
        return max(float(os.getenv("WOLF_VWAP_BREAK_PCT", "0.005")), 0.0)
    except (TypeError, ValueError):
        return 0.005


def vwap_break_rounds() -> int:
    """"持续跌破"的轮数确认（默认 2 轮；1 = 一破就走）。"""
    try:
        return max(int(float(os.getenv("WOLF_VWAP_BREAK_ROUNDS", "2"))), 1)
    except (TypeError, ValueError):
        return 2


def window_enabled() -> bool:
    """A4 做 T 时间窗语义（默认开）。`WOLF_RT_WINDOW=0` → 回 A1 原行为（任何时间达标即卖、不做到点决断）。"""
    return os.getenv("WOLF_RT_WINDOW", "1").strip() not in ("0", "false", "no")


def windows() -> list:
    """允许"目标兑现"的时间窗（狼大成文流程条件 2）。格式 "HH:MM-HH:MM,HH:MM-HH:MM"。"""
    raw = os.getenv("WOLF_RT_WINDOWS", "09:45-10:00,14:00-14:30")
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if "-" not in part:
            continue
        a, b = part.split("-", 1)
        a, b = _hhmm(a), _hhmm(b)
        if a is not None and b is not None:
            out.append((a, b))
    return out


def force_hm() -> Optional[int]:
    """到点决断时刻（分钟数），默认 14:00 = 他"2 点到了…先 T 了"。

    `WOLF_RT_FORCE_HM=off`（或空/0）→ 关闭"到点收工"（= 只保留窗口语义）。"""
    raw = os.getenv("WOLF_RT_FORCE_HM", "14:00")
    if str(raw).strip().lower() in ("", "off", "none", "0", "false", "no"):
        return None
    v = _hhmm(raw)
    return 840 if v is None else v


def timeout_tol() -> float:
    """"挣不到就亏个手续费出"的容忍浮亏（默认 0.5%）。"""
    try:
        return max(float(os.getenv("WOLF_RT_TIMEOUT_TOL", "0.005")), 0.0)
    except (TypeError, ValueError):
        return 0.005


def _hhmm(s) -> Optional[int]:
    """'14:00' / '1400' / '900' → 分钟数；解析不了返回 None。"""
    t = str(s or "").strip().replace("：", ":")
    if not t:
        return None
    if ":" in t:
        a, _, b = t.partition(":")
        try:
            return int(a) * 60 + int(b[:2])
        except (TypeError, ValueError):
            return None
    if t.isdigit() and len(t) in (3, 4):
        return int(t[:-2]) * 60 + int(t[-2:])
    return None


def in_window(hm) -> bool:
    """hm（"HH:MM"）是否落在做 T 时间窗内；hm 为空 → False（拿不到时间就不在窗口内动手）。"""
    m = _hhmm(hm)
    if m is None:
        return False
    return any(a <= m <= b for a, b in windows())


def past_force(hm) -> bool:
    f = force_hm()
    m = _hhmm(hm)
    return bool(f is not None and m is not None and m >= f)


def roundtrip_decision(cur: float, buy_avg: float, avg: Optional[float], up: float,
                       streak: int = 0, hm=None) -> Tuple[str, str, int]:
    """等量换手腿的离场判定（纯函数）。

    返回 (action, why, new_streak)；action ∈ {sell_target, sell_timebox, sell_vwap, wait}。
    · cur/buy_avg：现价 / 当日低吸均价；avg：分时均价线（黄线，quote.average）；up：兑现幅度（如 0.03）。
    · streak：**上一轮**连续在黄线下的次数（调用方持久化）。
    · hm：当前时刻 "HH:MM"（A4 时间窗用；None → 不在窗口内，也不做到点决断）。
    """
    if cur <= 0 or buy_avg <= 0:
        return ("wait", "无有效价格", streak)
    target = buy_avg * (1.0 + float(up))
    below = bool(avg and avg > 0 and cur < float(avg))
    new_streak = (streak + 1) if below else 0

    hit_target = cur >= target
    pct = vwap_break_pct()
    deep = bool(below and pct > 0 and avg and cur <= float(avg) * (1.0 - pct))
    sustained = bool(below and new_streak >= vwap_break_rounds())
    confirmed = bool(deep or sustained)
    why_c = ("突发跌破%.2f%%" % ((1 - cur / float(avg)) * 100)) if deep else \
            ("连续%d轮在黄线下" % new_streak) if sustained else ""

    if priority() == "vwap_first":          # 旧行为（回退开关）
        if confirmed:
            return ("sell_vwap", "黄线破位(%s) [vwap_first]" % why_c, new_streak)
        if hit_target:
            return ("sell_target", "达到兑现幅度 +%.1f%%" % (float(up) * 100), new_streak)
        return ("wait", "未达标且未破线", new_streak)

    win = window_enabled()
    # A1：**目标优先**（"没跌破就找这半小时的高点"）；A4：窗口外不因达标而卖（让他跑）
    if hit_target:
        if (not win) or in_window(hm):
            return ("sell_target", "达到兑现幅度 +%.1f%%（狼大 2026-08-13「3-5个点」）" % (float(up) * 100),
                    new_streak)
        # 窗口外达标 → 不卖，等窗口（14:00-14:30）再兑现；仍记录破线状态
        return ("wait", "已达标 +%.1f%% 但不在做T窗口(%s)，留到窗口再兑现（狼大 2025-04-15 条件2）"
                % ((cur / buy_avg - 1.0) * 100, _win_text()), new_streak)
    if confirmed:
        return ("sell_vwap", "黄线破位确认(%s)（狼大 2026-08-04「突发跌破直接走」）" % why_c, new_streak)
    # A4：到点决断 —— "2 点到了 力度不够 我先把早上博弈的先T了…结束今天做T操作"（2026-09-02 14:03）
    if win and past_force(hm):
        floor = buy_avg * (1.0 - timeout_tol())
        if cur >= floor:
            return ("sell_timebox",
                    "到 %s 未达标(现价 %+.2f%%)，按狼大 2026-09-02「力度不够先T了、结束今天做T操作」收工"
                    % (_fmt_hm(force_hm()), (cur / buy_avg - 1.0) * 100), new_streak)
        return ("wait", "到点但浮亏 %.2f%% 超过容忍 %.1f%%（交给保护腿）"
                % ((cur / buy_avg - 1.0) * 100, timeout_tol() * 100), new_streak)
    return ("wait", ("在黄线下第%d轮（未确认）" % new_streak) if below else "未达标且未破线", new_streak)


def _fmt_hm(m: Optional[int]) -> str:
    return "—" if m is None else "%02d:%02d" % (m // 60, m % 60)


def _win_text() -> str:
    return ",".join("%02d:%02d-%02d:%02d" % (a // 60, a % 60, b // 60, b % 60) for a, b in windows())

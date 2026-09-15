# -*- coding: utf-8 -*-
"""「选板块第一要素」门（2026-09-15 参数对齐）的定向单测。

钉子（全部对回原话，不留自设数值）：
  狼大 2025-06-16（逐字）「量能活跃(也就是最近一周内至少2/3天数以上在10日量能以上)，
  资金没有5日连续流出的。这是选板块的第一要素」→ 四个数：5 / 10 / 2÷3 / 5。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import wolf_theme_vol_fund as V  # noqa: E402


def test_constants_match_quote():
    """四个数字必须来自那一句原话，不得被改。"""
    assert V.ACT_DAYS == 5 and V.VOL_WIN == 10 and V.FUND_DAYS == 5
    assert V.ACT_NEED == 4          # ceil(5 × 2/3)
    assert "第一要素" in (V.__doc__ or "")


def test_gate_default_off(monkeypatch):
    monkeypatch.delenv("WOLF_THEME_VOLFUND_GATE", raising=False)
    assert V.enabled() is False                       # 默认关（开启=行为变更，需拍板）
    monkeypatch.setenv("WOLF_THEME_VOLFUND_GATE", "1")
    assert V.enabled() is True


def _amounts(act_days_above, base=10.0, n=17):
    """构造序列：末尾 5 日中有 act_days_above 天放量（> MA10），其余缩量。"""
    a = [base] * n
    for k in range(5):
        if k < act_days_above:
            a[n - 5 + k] = base * 1.5
        else:
            a[n - 5 + k] = base * 0.5
    return a


def test_vol_active_4of5_passes_3of5_fails():
    """近一周"至少2/3天数"= 4 天达标 → 过；3 天 → 不过。"""
    ok, why, d = V.check(_amounts(4), [1.0] * 6)
    assert ok is True and d["active_days"] == 4
    ok3, why3, d3 = V.check(_amounts(3), [1.0] * 6)
    assert ok3 is False and d3["active_days"] == 3 and "量能不活跃" in why3


def test_fund_5day_outflow_blocks_4day_ok():
    """资金连续 5 日净流出（**数值不同**，非填充）→ 拦；只连续 4 日 → 放行。"""
    ok, why, _ = V.check(_amounts(4), [-1.0, -2.0, -1.5, -0.5, -3.0])
    assert ok is False and "连续5日净流出" in why
    ok4, _, _ = V.check(_amounts(4), [-1.0, -2.0, -1.5, -0.5, 0.5])
    assert ok4 is True


def test_stale_fund_series_fails_open():
    """资金序列全同（生产 concept_hist 前值填充实测）→ 资金半边放行，不得据此拦。"""
    ok, why, d = V.check(_amounts(4), [-9.0, -9.0, -9.0, -9.0, -9.0])
    assert ok is True and d.get("fund_data") == "stale(all-equal)" and "前值填充" in why


def test_fail_open_on_missing_data():
    """数据不足 → 放行（数据问题不封死买路，与 theme_buyable 既有约定一致）。"""
    ok, why, _ = V.check([], [])
    assert ok is True and "不足" in why
    ok2, _, _ = V.check([1.0] * 3, [1.0] * 3)
    assert ok2 is True


def test_threshold_is_ma10_including_today():
    """边界：当日恰等于 MA10 → **不算**活跃（需严格大于）；略高 → 算。"""
    base = [10.0] * 14
    flat = base + [10.0]                 # 15 个点，当日 = MA10（10.0）→ 严格大于才活跃 → 0
    ok, _, d = V.check(flat, [1.0] * 6)
    assert d["active_days"] == 0 and ok is False
    above = base + [10.01]
    _, _, d2 = V.check(above, [1.0] * 6)
    assert d2["active_days"] == 1
    # 序列不足以给每天都算出 10 日基准（会拿空窗口当 0）→ 必须放行，不得误判为活跃
    ok3, why3, d3 = V.check([10.0] * 12, [1.0] * 6)
    assert ok3 is True and "不足" in why3 and "active_days" not in d3


def test_theme_buyable_wiring_is_opt_in():
    """接线必须是**可选**的（默认不改行为），且原因串进 theme_buyable 的 reason。"""
    src = open(os.path.join(ROOT, "apps", "main_line", "wolf_context.py"), encoding="utf-8").read()
    assert "from wolf_theme_vol_fund import" in src and "theme_volfund_ok" in src
    assert "_vf_on()" in src                          # 由开关控制（默认关，见本模块 enabled()）
    vf = open(os.path.join(ROOT, "apps", "main_line", "wolf_theme_vol_fund.py"), encoding="utf-8").read()
    assert 'WOLF_THEME_VOLFUND_GATE", "0"' in vf      # 默认值就是 "0"（关）
    assert "2025-06-16" in vf                         # 原话日期在代码里（可追溯）

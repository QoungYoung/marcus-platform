# -*- coding: utf-8 -*-
"""L2 极端超跌豁免逻辑测试（2026-08-28 全A回放结论落地）。

覆盖：
- indicator._eval_l2_oversold_exempt：L1 已过 + 前5日跌幅≥15% 判定
- long_term_pool_monitor._accept_entry_grade：probe_only 仅在豁免标记下放行
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]          # backend/
_CORE = Path(__file__).resolve().parents[2] / "core"
for _p in (_BACKEND, _CORE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.api.indicator import _eval_l2_oversold_exempt, _l2_gate_disabled
from app.services.long_term_pool_monitor import _accept_entry_grade, _etf_pivot_enabled, _rank_candidate


class _FakeResult:
    def __init__(self, final_grade="pass", l2_oversold_exempt=False):
        self.final_grade = final_grade
        self.l2_oversold_exempt = l2_oversold_exempt


# ── _eval_l2_oversold_exempt ──

def test_exempt_requires_l1_pass_and_deep_drop():
    assert _eval_l2_oversold_exempt(True, -0.20) is True
    assert _eval_l2_oversold_exempt(True, -0.15) is True
    assert _eval_l2_oversold_exempt(True, -0.1499) is False
    assert _eval_l2_oversold_exempt(True, -0.10) is False
    assert _eval_l2_oversold_exempt(True, 0.05) is False


def test_exempt_requires_l1_pass():
    assert _eval_l2_oversold_exempt(False, -0.25) is False
    assert _eval_l2_oversold_exempt(False, -0.40) is False


def test_exempt_handles_missing_data():
    assert _eval_l2_oversold_exempt(True, None) is False
    assert _eval_l2_oversold_exempt(False, None) is False


# ── _l2_gate_disabled（试运行开关）──

def test_l2_gate_disabled_default_off(monkeypatch):
    monkeypatch.delenv("ENTRY_L2_DISABLED", raising=False)
    assert _l2_gate_disabled() is False


def test_l2_gate_disabled_flag(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("ENTRY_L2_DISABLED", v)
        assert _l2_gate_disabled() is True
    monkeypatch.setenv("ENTRY_L2_DISABLED", "0")
    assert _l2_gate_disabled() is False


# ── _etf_pivot_enabled（弱市切红利ETF 开关）──

def test_etf_pivot_enabled_default_off(monkeypatch):
    monkeypatch.delenv("REGIME_DIVIDEND_ETF_ENABLED", raising=False)
    assert _etf_pivot_enabled() is False


def test_etf_pivot_enabled_flag(monkeypatch):
    for v in ("1", "true", "on"):
        monkeypatch.setenv("REGIME_DIVIDEND_ETF_ENABLED", v)
        assert _etf_pivot_enabled() is True
    monkeypatch.setenv("REGIME_DIVIDEND_ETF_ENABLED", "0")
    assert _etf_pivot_enabled() is False


# ── _rank_candidate（当日最优前 N 排序）──

class _FakeTech:
    macd_status = "金叉"
    ma_status = "MA5>MA20"
    intraday_percentile = 30
    current_price = 10.0


class _FakeCapital:
    d5_main_net = 1e8
    today_main_net = 2e7
    grade = "✅通过"


class _FakeLayer:
    passed = True
    grade = "✅通过"


class _FakeBuyConf:
    change_pct = 2.0


class _FakeResult2:
    downgrade_multiplier = 1.0
    tech = _FakeTech()
    layer1_tech = _FakeLayer()
    layer2_capital = _FakeCapital()
    layer3_overbought = _FakeLayer()
    buy_confirmation = _FakeBuyConf()
    l2_oversold_exempt = False


def test_rank_prefers_strong_signal_low_chase():
    r = _FakeResult2()
    score = _rank_candidate(r)
    assert score >= 1.2  # 1.0基础 + L1过0.15 + 金叉0.1 + MA多头0.1 + 5日主力0.1 + 今日0.05 - 分位0.09


def test_rank_penalizes_high_percentile_and_big_gain():
    r = _FakeResult2()
    r.tech.intraday_percentile = 95
    r.buy_confirmation.change_pct = 7.0
    r.downgrade_multiplier = 0.5
    low = _rank_candidate(r)
    r2 = _FakeResult2()
    high = _rank_candidate(r2)
    assert low < high


# ── _accept_entry_grade ──

def test_accept_pass():
    assert _accept_entry_grade(_FakeResult("pass", False)) is True
    assert _accept_entry_grade(_FakeResult("pass", True)) is True


def test_accept_probe_only_only_with_exempt_flag():
    assert _accept_entry_grade(_FakeResult("probe_only", True)) is True
    assert _accept_entry_grade(_FakeResult("probe_only", False)) is False


def test_reject_other_grades():
    assert _accept_entry_grade(_FakeResult("blocked", True)) is False
    assert _accept_entry_grade(_FakeResult("blocked", False)) is False
    assert _accept_entry_grade(_FakeResult("downgraded", True)) is False
    assert _accept_entry_grade(_FakeResult("", False)) is False

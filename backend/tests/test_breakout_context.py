# -*- coding: utf-8 -*-
"""breakout_context：B4/B5 binary 与收盘强弱事实底稿的单测（不联网）。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
import breakout_context as bc  # noqa: E402


def test_b4_b5_mainline_ok(monkeypatch):
    vals = {"sh512480": 0.05, "sh588170": 0.03, "sh512800": -0.006, "sh518880": 0.004}

    def fake(code, day):
        return vals.get(code)

    monkeypatch.setattr(bc, "day_return", fake)
    r = bc.b4_b5("半导体/芯片", "2026-04-30")
    assert r["mainline_ok"] is True
    assert r["hedge_flag"] is False
    assert r["mainline_ret"] == 0.04


def test_b4_b5_hedge_flag(monkeypatch):
    vals = {"sh512480": -0.006, "sh588170": -0.013, "sh512800": -0.005, "sh518880": 0.023}

    def fake(code, day):
        return vals.get(code)

    monkeypatch.setattr(bc, "day_return", fake)
    r = bc.b4_b5("AI/算力/科技", "2026-09-03")
    assert r["mainline_ok"] is False
    assert r["hedge_flag"] is True


def test_close_strength_facts_raw(monkeypatch):
    bars = {}
    for i in range(25):
        d = "202506%02d" % (i + 1)
        bars[d] = {"open": 3397.0, "high": 3401.0, "low": 3370.0, "close": 3377.0, "amount": 1.0}
    bars["20250613"] = {"open": 3397.48, "high": 3401.55, "low": 3370.59, "close": 3377.0, "amount": 1.0}
    monkeypatch.setattr(bc, "fetch_daily", lambda code, n=800: bars)
    f = bc.close_strength_facts("sh000001", "2025-06-13")
    assert f["ok"] is True
    assert abs(f["close_pos_raw"] - 0.207) < 0.01
    assert abs(f["upper_shadow_pct"] - 13.15) < 0.1
    assert "close_pos_raw" in f and "intraday" in f


def test_agent_payload_contract(monkeypatch):
    monkeypatch.setattr(bc, "close_strength_facts", lambda code, day: {"ok": True, "close": 1.0})
    monkeypatch.setattr(bc, "b4_b5", lambda theme, day: {"mainline_ok": True, "hedge_flag": False})
    p = bc.agent_payload("sh000001", "2026-04-30", "半导体/芯片")
    assert p["task"] == "judge_close_strength"
    assert p["allowed_labels"] == ["strong", "weak", "neutral", "no_confident"]
    assert "output_schema" in p
    assert "score" not in str(p).lower()


def test_retest_state():
    assert bc.retest_state(100, 99.5, 100.2) == "hold"
    assert bc.retest_state(100, 99.5, 99.8) == "break_pending"
    assert bc.retest_state(100, 99.5, 99.8, 100.1) == "false_break"
    assert bc.retest_state(100, 99.5, 99.8, 99.7) == "break_confirmed"
    assert bc.retest_state(100, 100.5, 101.0) == "no_touch"


def _bars_with_retest(retest_close, next_close=None):
    bars = {}
    for i in range(1, 61):
        d = "202501%02d" % i if i <= 31 else "202502%02d" % (i - 31)
        bars[d] = {"open": 100, "high": 101, "low": 99, "close": 100, "amount": 1}
    # 连续日期更稳：用 20250101..20250301
    import datetime
    bars = {}
    d0 = datetime.date(2025, 1, 1)
    for i in range(60):
        d = (d0 + datetime.timedelta(days=i)).strftime("%Y%m%d")
        bars[d] = {"open": 100, "high": 101, "low": 99, "close": 100, "amount": 1}
    b = (d0 + datetime.timedelta(days=60)).strftime("%Y%m%d")
    bars[b] = {"open": 100, "high": 106, "low": 100, "close": 106, "amount": 3}
    r = (d0 + datetime.timedelta(days=61)).strftime("%Y%m%d")
    bars[r] = {"open": 106, "high": 106, "low": 99, "close": retest_close, "amount": 1}
    if next_close is not None:
        n = (d0 + datetime.timedelta(days=62)).strftime("%Y%m%d")
        bars[n] = {"open": retest_close, "high": max(retest_close, next_close) + 1,
                   "low": min(retest_close, next_close) - 1, "close": next_close, "amount": 1}
    return bars, b, r


def test_b8_state_from_bars_hold():
    bars, b, r = _bars_with_retest(106)
    out = bc.b8_state_from_bars(bars, r)
    assert out["state"] == "hold" and out["level"] == 100


def test_b8_state_from_bars_confirmed():
    bars, b, r = _bars_with_retest(98, 98)
    out = bc.b8_state_from_bars(bars, (r[:6] + "03") if False else sorted(bars)[-1])
    assert out["state"] == "break_confirmed"


def test_b8_state_from_bars_false_break():
    bars, b, r = _bars_with_retest(98, 101)
    out = bc.b8_state_from_bars(bars, sorted(bars)[-1])
    assert out["state"] == "false_break"


def test_b8_gate_states(monkeypatch):
    monkeypatch.setattr(bc, "b8_state", lambda code, day=None: {"state": "break_confirmed"})
    g = bc.b8_gate("600000", "2026-09-03")
    assert g["action"] == "block" and g["allow"] is False
    monkeypatch.setattr(bc, "b8_state", lambda code, day=None: {"state": "break_pending"})
    assert bc.b8_gate("600000", "2026-09-03")["action"] == "downgrade"
    monkeypatch.setattr(bc, "b8_state", lambda code, day=None: {"state": "hold"})
    assert bc.b8_gate("600000", "2026-09-03")["action"] == "pass"

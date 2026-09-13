# -*- coding: utf-8 -*-
"""t_vreb_reversal 量窒息+顺风反包 → 做T底仓建仓候选 模块测试。"""
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import t_vreb_reversal as m


def test_is_main_board():
    assert m._is_main_board("600519.SH") is True
    assert m._is_main_board("000001.SZ") is True
    assert m._is_main_board("002594.SZ") is True
    assert m._is_main_board("300750.SZ") is False   # 创业板
    assert m._is_main_board("301236.SZ") is False   # 创业板
    assert m._is_main_board("688981.SH") is False   # 科创板
    assert m._is_main_board("689009.SH") is False   # 科创板


def test_regime_classifier_daily():
    assert m._regime_classifier_daily(1.0, True) == "趋势向上"
    assert m._regime_classifier_daily(-1.0, False) == "趋势向下"
    assert m._regime_classifier_daily(0.2, True) == "震荡"        # 斜率小
    assert m._regime_classifier_daily(1.0, False) == "震荡"       # 价在MA20下但均线向上
    assert m._regime_classifier_daily(float("nan"), True) == "震荡"


def test_monthly_regime_shape():
    r = m.monthly_regime(datetime.date(2026, 8, 31))
    assert r["regime"] in ("趋势向上", "震荡", "趋势向下")
    assert r["month"] == "2026-08"
    assert isinstance(r["map"], dict) and len(r["map"]) > 100


def test_compute_candidates_shape():
    cands = m.compute_vreb_reversal_candidates(datetime.date(2026, 6, 25), top_n=5)
    assert isinstance(cands, list)
    assert len(cands) <= 5
    for c in cands:
        assert c["trend"] == "多头"
        assert c["source"] == "vreb_reversal"
        assert 0.0 <= c["score"] <= 1.0
        assert c["month_regime"] in ("趋势向上", "震荡", "趋势向下")
        # 主板 + 剔一字板：不含创业板/科创板
        assert m._is_main_board(c["symbol"]) is True


def test_confirmation_window_conditions():
    w = m.confirmation_window_conditions("600519.SH", "2026-06-25", 100.0)
    assert w["window_days"] == 5
    assert w["confirm_date"] == "2026-06-25"
    assert w["low_buy"]["and"][0]["field"] == "quote.current"
    assert w["low_buy"]["and"][0]["value"] == 97.0
    assert w["high_sell"]["and"][0]["value"] == 103.0


def test_persist_skips_when_not_trend_up(monkeypatch):
    # 强制候选为『震荡』月 → require_trend_up 时跳过，不触库
    fake = [{"trade_date": "2026-06-25", "symbol": "600519.SH", "score": 0.5,
             "reasons": ["顺风"], "trend": "多头", "source": "vreb_reversal",
             "month_regime": "震荡"}]
    monkeypatch.setattr(m, "compute_vreb_reversal_candidates", lambda *a, **k: fake)
    res = m.persist_vreb_candidates(trade_date=datetime.date(2026, 6, 25), require_trend_up=True)
    assert res["skipped"] is True
    assert res["written"] == []
    assert "趋势向上" in res["note"]


def test_score_range():
    import pandas as pd
    row = pd.Series({"cs": 0.98, "pct1": 4.0, "vr_min": 0.8, "ind_pct_T1": 3.0,
                     "mkt_pct": 1.5, "amp": 0.04})
    s = m._score(row)
    assert 0.0 <= s <= 1.0


def test_is_enabled(monkeypatch):
    assert m.is_enabled() is True
    monkeypatch.setenv("VREB_REVERSAL_ENABLED", "0")
    assert m.is_enabled() is False


def test_vreb_regime_gate():
    assert m.vreb_regime_gate({"halt": True}) == "BLOCKED"
    assert m.vreb_regime_gate({"below_ma20": True}) == "BLOCKED"
    assert m.vreb_regime_gate({"day_drop": -1.0}) == "BLOCKED"
    assert m.vreb_regime_gate({"industry_breadth": 0.4}) == "BLOCKED"
    assert m.vreb_regime_gate({"gate_low_buy": "ALLOWED", "halt": False}) == "ALLOWED"


def test_validate_tail_close_reference():
    # 远超收盘的价 → 拒绝(防前视)；正常应答含 ok/close/dev_pct
    r = m.validate_tail_close_reference("600519.SH", datetime.date(2026, 6, 25), 99999.0)
    assert "ok" in r and "dev_pct" in r
    assert r["ok"] is False if r.get("close") else r["ok"] is True


def test_auto_gen_vreb_window_conditions(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.t_db.upsert_condition",
                        lambda cond: (calls.append(cond), 123)[1])
    out = m.auto_gen_vreb_window_conditions("600519.SH", "2026-06-25", 100.0)
    assert len(calls) == 2            # low_buy + high_sell
    assert out["created"][0]["direction"] == "buy"
    assert out["created"][1]["direction"] == "sell"
    assert out["created"][0]["condition_id"] == 123

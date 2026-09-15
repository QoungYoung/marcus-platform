# -*- coding: utf-8 -*-
"""C4 参数对齐：ETF 波动下限 ≥3%（2026-08-21 原话）的定向单测。

狼大逐字：「选半导体仅仅只是因为他**波动大 ETF都有3个点以上的波动** 不然选个别的1个点的ETF没意思」
→ 阈值 3.0（他的数）；**只对 ETF 生效**（他的话讲的就是 ETF；个股不套用）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import wolf_etf_vol as V  # noqa: E402


def _bars(amp_pct, n=25):
    """构造振幅为 amp_pct 的日线。"""
    out = []
    for i in range(n):
        c = 1.0
        out.append({"trade_date": "202609%02d" % (i + 1), "close": c,
                    "high": c * (1 + amp_pct / 200.0), "low": c * (1 - amp_pct / 200.0)})
    return out


def test_threshold_is_corpus_value():
    assert V.VOL_THR == 3.0          # 他的话："3个点以上"
    assert "2026-08-21" in (V.__doc__ or "")


def test_gate_default_off(monkeypatch):
    monkeypatch.delenv("WOLF_ETF_VOL_GATE", raising=False)
    assert V.enabled() is False
    monkeypatch.setenv("WOLF_ETF_VOL_GATE", "1")
    assert V.enabled() is True


def test_etf_detection():
    assert V.is_etf("SH512480") and V.is_etf("SZ159825") and V.is_etf("588170.SH")
    assert not V.is_etf("SH600108") and not V.is_etf("SZ300189")


def test_amplitude_and_threshold():
    ok, why, d = V.check(_bars(4.0))
    assert ok is True and d["amp"] >= 3.0
    ok2, why2, d2 = V.check(_bars(1.0))          # 他说的"1个点的ETF没意思"
    assert ok2 is False and d2["amp"] < 3.0 and "近20日日均振幅" in why2
    # 边界：恰好 3.0 → 达标（他的话是"3个点以上"）
    ok3, _, d3 = V.check(_bars(3.0))
    assert ok3 is True and abs(d3["amp"] - 3.0) < 0.01


def test_fail_open_on_missing_data():
    ok, why, d = V.check([])
    assert ok is True and d["amp"] is None and "不足" in why


def test_non_etf_passthrough(monkeypatch):
    ok, why = V.etf_vol_ok("SH600108")
    assert ok is True and "非 ETF" in why


def test_wired_into_gateway_but_opt_in():
    src = open(os.path.join(ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "wolf_etf_vol" in src and "WOLF_ETF_VOL_GATE" in src or "enabled as _ev_on" in src
    assert 'if _ev_on() and _is_etf(symbol)' in src     # 只对 ETF、且需开关

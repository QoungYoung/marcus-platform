# -*- coding: utf-8 -*-
"""共享买入前门：意图分流 / hard_block / multiplier。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.services import pre_trade_gate as ptg  # noqa: E402
from app.services import p2_entry_gate as pg  # noqa: E402


def test_normalize_ts_code():
    assert ptg.normalize_ts_code("SZ600000") == "600000.SH"
    assert ptg.normalize_ts_code("600000.SH") == "600000.SH"
    assert ptg.normalize_ts_code("600000") == "600000.SH"
    assert ptg.normalize_ts_code("000001") == "000001.SZ"


def test_sell_intent_bypasses_gate(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("sell 不应调用 p2_gate_check")
    monkeypatch.setattr(pg, "p2_gate_check", boom)
    monkeypatch.setattr(ptg, "_log", lambda *a, **k: None)
    g = ptg.pre_trade_gate("SH600000", intent="sell")
    assert g["allow"] is True and g["hard_block"] is False


def test_hard_block(monkeypatch):
    monkeypatch.setattr(ptg, "_log", lambda *a, **k: None)
    monkeypatch.setattr(pg, "p2_gate_check", lambda symbol=None, ts_code=None: {
        "hard_block": True, "multiplier": 1.0, "reasons": ["B8 跌破确认"],
        "b4b5": {"action": "pass"}, "b8": {"action": "block"}})
    g = ptg.pre_trade_gate("SZ300304", intent="probe", source="test")
    assert g["allow"] is False and g["hard_block"] is True
    assert "B8 跌破确认" in g["reasons"]


def test_downgrade_multiplier(monkeypatch):
    monkeypatch.setattr(ptg, "_log", lambda *a, **k: None)
    monkeypatch.setattr(pg, "p2_gate_check", lambda symbol=None, ts_code=None: {
        "hard_block": False, "multiplier": 0.5, "reasons": ["B4 false"],
        "b4b5": {"action": "downgrade"}, "b8": {"action": "pass"}})
    g = ptg.pre_trade_gate("SZ300304", intent="new_base")
    assert g["allow"] is True and g["multiplier"] == 0.5

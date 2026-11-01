# -*- coding: utf-8 -*-
"""交易接口买侧硬门：enforce/shadow/off、sell 放行、t_buy 放行。"""
import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.api import trades as tr  # noqa: E402
from app.services import pre_trade_gate as ptg  # noqa: E402


def _trade(**kw):
    base = dict(side="buy", symbol="SZ300304", price=10.0, volume=1000, intent="probe")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_sell_bypass(monkeypatch):
    monkeypatch.setenv("WOLF_PRETRADE_ORDER_GATE", "enforce")
    monkeypatch.setattr(ptg, "pre_trade_gate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调用")))
    ok, why, info = tr._pretrade_gate_for_trade(_trade(side="sell"))
    assert ok is True and info is None


def test_t_buy_bypass(monkeypatch):
    monkeypatch.setenv("WOLF_PRETRADE_ORDER_GATE", "enforce")
    monkeypatch.setattr(ptg, "pre_trade_gate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调用")))
    ok, why, info = tr._pretrade_gate_for_trade(_trade(intent="t_buy"))
    assert ok is True and info is None


def test_enforce_block(monkeypatch):
    monkeypatch.setenv("WOLF_PRETRADE_ORDER_GATE", "enforce")
    monkeypatch.setattr(ptg, "pre_trade_gate", lambda *a, **k: {
        "allow": False, "multiplier": 1.0, "reasons": ["B8 跌破确认"]})
    ok, why, info = tr._pretrade_gate_for_trade(_trade())
    assert ok is False and "B8" in why and info["allow"] is False


def test_shadow_allow(monkeypatch):
    monkeypatch.setenv("WOLF_PRETRADE_ORDER_GATE", "shadow")
    monkeypatch.setattr(ptg, "pre_trade_gate", lambda *a, **k: {
        "allow": False, "multiplier": 1.0, "reasons": ["B5 避险抢戏"]})
    ok, why, info = tr._pretrade_gate_for_trade(_trade())
    assert ok is True and "SHADOW" in why


def test_off_bypass(monkeypatch):
    monkeypatch.setenv("WOLF_PRETRADE_ORDER_GATE", "off")
    monkeypatch.setattr(ptg, "pre_trade_gate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调用")))
    ok, why, info = tr._pretrade_gate_for_trade(_trade())
    assert ok is True and info is None


def test_downgrade_warning(monkeypatch):
    monkeypatch.setenv("WOLF_PRETRADE_ORDER_GATE", "enforce")
    monkeypatch.setattr(ptg, "pre_trade_gate", lambda *a, **k: {
        "allow": True, "multiplier": 0.5, "reasons": ["B4 false"]})
    ok, why, info = tr._pretrade_gate_for_trade(_trade())
    assert ok is True and "降级" in why

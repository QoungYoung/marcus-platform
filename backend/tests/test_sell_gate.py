# -*- coding: utf-8 -*-
"""卖出前门：狼大止损线 / 重复卖出 / 交易接口接线。"""
import datetime as dt
import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.services import sell_gate as sg  # noqa: E402
from app.api import trades as tr  # noqa: E402


def _ctx(**kw):
    base = dict(symbol="SZ300304", price=9.0, volume=1000, intent="stop_loss",
                now=dt.datetime(2026, 1, 12, 15, 5), ma5=9.5, ma13=9.6, ma34=9.8,
                ma60=10.0, last_open=9.4, last_close=9.0, vol_ratio=1.2,
                b8_state="", sells_today=0, v3_today=False, action_key_dup=False)
    base.update(kw)
    return sg.SellContext(**base)


def test_classify_intent():
    assert sg.classify_intent("stop_loss") == "stop_loss"
    assert sg.classify_intent("", "跌破止损") == "stop_loss"
    assert sg.classify_intent("", "减仓避一下") == "reduce"
    assert sg.classify_intent("", "止盈兑现") == "take_profit"
    assert sg.classify_intent("", "做T出") == "t_sell"


def test_stop_loss_ma60_ok():
    ok, why = sg.validate_stop_loss(_ctx())
    assert ok is True and "MA60" in why[0]


def test_stop_loss_no_line():
    ok, why = sg.validate_stop_loss(_ctx(price=10.5, ma5=9.5, ma34=9.8, ma60=10.0))
    assert ok is False and "未触发狼大止损线" in why[0]


def test_stop_loss_false_break():
    ok, why = sg.validate_stop_loss(_ctx(b8_state="false_break"))
    assert ok is False and "假跌破" in why[0]


def test_stop_loss_low_volume():
    ok, why = sg.validate_stop_loss(_ctx(vol_ratio=0.5))
    assert ok is False and "地量" in why[0]


def test_stop_loss_time_window():
    ok, why = sg.validate_stop_loss(_ctx(now=dt.datetime(2026, 1, 12, 13, 30)))
    assert ok is False and "13:00-14:30" in why[0]


def test_stop_loss_wait_close():
    ok, why = sg.validate_stop_loss(_ctx(now=dt.datetime(2026, 1, 12, 10, 0), last_close=10.5))
    assert ok is False and "收盘未确认" in why[0]


def test_stop_loss_morning_prior_close():
    ok, why = sg.validate_stop_loss(_ctx(now=dt.datetime(2026, 1, 12, 10, 0), last_close=9.0, price=9.1))
    assert ok is True and "MA60" in why[0]


def test_stop_loss_index_break():
    ok, why = sg.validate_stop_loss(_ctx(now=dt.datetime(2026, 1, 12, 10, 0), index_break=True))
    assert ok is True and "指数大级别" in why[0]


def test_duplicate_cap():
    allow, why, _ = sg.validate_sell(_ctx(sells_today=2))
    assert allow is False and "已卖 2 笔" in why[0]


def test_duplicate_v3_and_key():
    assert sg.validate_sell(_ctx(v3_today=True))[0] is False
    assert sg.validate_sell(_ctx(action_key_dup=True))[0] is False


def test_take_profit_passes():
    allow, why, it = sg.validate_sell(_ctx(intent="take_profit"))
    assert allow is True and it == "take_profit"


def test_trades_sell_gate_enforce(monkeypatch):
    monkeypatch.setenv("WOLF_SELL_GATE", "enforce")
    monkeypatch.setattr(sg, "sell_gate", lambda *a, **k: {
        "allow": False, "reasons": ["未触发狼大止损线"]})
    t = types.SimpleNamespace(side="sell", symbol="SZ300304", price=9.0, volume=1000,
                              intent="stop_loss", reason="止损", account="stock")
    ok, why, info = tr._sell_gate_for_trade(t)
    assert ok is False and "未触发" in why


def test_trades_sell_gate_shadow(monkeypatch):
    monkeypatch.setenv("WOLF_SELL_GATE", "shadow")
    monkeypatch.setattr(sg, "sell_gate", lambda *a, **k: {
        "allow": False, "reasons": ["地量不割肉"]})
    t = types.SimpleNamespace(side="sell", symbol="SZ300304", price=9.0, volume=1000,
                              intent="stop_loss", reason="止损", account="stock")
    ok, why, info = tr._sell_gate_for_trade(t)
    assert ok is True and "SHADOW" in why


def test_stop_loss_data_missing_fail_open():
    ok, why = sg.validate_stop_loss(_ctx(ma5=None, ma34=None, ma60=None))
    assert ok is True and "fail-open" in why[0]

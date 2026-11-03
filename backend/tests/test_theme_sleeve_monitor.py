# -*- coding: utf-8 -*-
from app.services.theme_sleeve_monitor import ThemeSleeveMonitor


def test_sell_rejects_outside_window():
    m = ThemeSleeveMonitor(executor=None)
    original = type(m)._exec_window
    type(m)._exec_window = classmethod(lambda cls: False)
    try:
        r = m._sell("SZ002086", 3.07, 2400, "theme_monitor:up_resistance")
        assert r["status"] == "blocked"
        assert r["reason"] == "outside_theme_window"
    finally:
        type(m)._exec_window = original


def test_sell_passes_window_to_executor():
    class E:
        def sell(self, *a, **k):
            return {"status": "ok"}
    m = ThemeSleeveMonitor(executor=E())
    original = type(m)._exec_window
    type(m)._exec_window = classmethod(lambda cls: True)
    try:
        assert m._sell("SZ002086", 3.07, 100, "theme_monitor:up_resistance") == {"status": "ok"}
    finally:
        type(m)._exec_window = original


def test_down_break_rejects_one_cent_false_break():
    m = ThemeSleeveMonitor(executor=None)
    q = {"current": 3.41, "pre_close": 3.11, "high": 3.42}
    # 3.41 只比 3.42 低一分钱，在 0.3% 缓冲内且贴涨停 → 不卖
    assert m._down_break_confirmed(q, 3.41, 3.42, "SZ002086") is False


def test_down_break_accepts_real_break():
    m = ThemeSleeveMonitor(executor=None)
    q = {"current": 3.40, "pre_close": 3.11, "high": 3.41}
    # 3.40 已跌破 3.42 的 0.3% 缓冲 → 确认
    assert m._down_break_confirmed(q, 3.40, 3.42, "SZ002086") is True

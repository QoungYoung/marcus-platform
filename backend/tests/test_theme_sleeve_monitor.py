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

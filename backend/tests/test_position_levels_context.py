# -*- coding: utf-8 -*-
"""持仓点位注入交易 agent：down/up + 现价距离。"""
import datetime as dt
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.services import trade_graph as tg  # noqa: E402
from app.services import position_levels_store as pls  # noqa: E402


def test_position_levels_context(monkeypatch):
    monkeypatch.setattr(pls, "load", lambda account="stock": {
        "SH600000": {"down": 100.0, "up": 120.0, "status": "holding",
                     "updated_at": dt.datetime(2026, 10, 30, 19, 10)}})
    monkeypatch.setattr(tg, "_read_portfolio", lambda: json.dumps({
        "positions": [{"symbol": "SH600000", "volume": 1000, "avg_cost": 98.0}]}))
    import app.services.t_data_sources as tds
    monkeypatch.setattr(tds, "fetch_quote_one", lambda sym: {"current": 105.0})
    txt = tg._read_position_levels_context("stock")
    assert "down=100.00" in txt and "up=120.00" in txt
    assert "现价105.00" in txt and "距down +5.0%" in txt
    assert "距up -12.5%" in txt
    assert "不替代" in txt


def test_position_levels_empty(monkeypatch):
    monkeypatch.setattr(pls, "load", lambda account="stock": {})
    txt = tg._read_position_levels_context("stock")
    assert "未生成" in txt

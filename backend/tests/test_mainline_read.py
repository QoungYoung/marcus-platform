# -*- coding: utf-8 -*-
"""mainline_read 读取层的最小单测（不依赖 DB/实时行情）。"""
from app.models.indicator import CalcPositionRequest
from app.services.mainline_read import load_position_context


def test_context_by_theme():
    ctx = load_position_context(theme_id="storage")
    assert ctx["available"] is True
    assert ctx["theme_id"] == "storage"
    assert ctx["caps"].get("max_single_stock", 0) > 0
    assert "thesis_valid" in ctx
    assert ctx["holding_days"] == 5


def test_context_by_symbol():
    ctx = load_position_context(symbol="688836.SH")
    assert ctx["available"] is True
    assert ctx["theme_id"] == "humanoid_robot"
    assert ctx["caps"].get("max_weight", 0) > 0


def test_request_accepts_theme_and_track():
    req = CalcPositionRequest(symbol="600519.SH", theme_id="storage", track_id="semiconductor")
    assert req.theme_id == "storage"
    assert req.track_id == "semiconductor"

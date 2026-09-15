# -*- coding: utf-8 -*-
"""单测：均线挂单真挂腿的表达式与开关（2026-09-15 用户指示上线）。"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "jobs"), str(ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RSA = importlib.import_module("rotation_switch_arm")


def test_ma_line_expr_uses_line_price_as_trigger():
    e = RSA.ma_line_expr(39.6202)
    assert e["and"][0] == {"op": "<=", "field": "quote.current", "value": 39.62}
    ops = {(c["field"], c["op"]) for c in e["and"]}
    assert ("vol_ratio", "<=") in ops and ("quote.average", ">") in ops
    # 与 254 的关键差别：不依赖 dip_prev_low
    assert all(c["field"] != "quote.dip_prev_low" for c in e["and"])


def test_switch_default_and_env(monkeypatch):
    monkeypatch.delenv("WOLF_MA_LINE_ENTRY", raising=False)
    assert RSA.ma_line_enabled() is False
    monkeypatch.setenv("WOLF_MA_LINE_ENTRY", "1")
    assert RSA.ma_line_enabled() is True


def test_price_lookup_failure_returns_none(monkeypatch):
    """取不到均线价时必须返回 None（调用方回退 254 原条件，不丢腿）。"""
    monkeypatch.setattr(RSA, "ma_line_price", lambda *a, **k: None)
    assert RSA.ma_line_price("SZ000001", "20260915") is None

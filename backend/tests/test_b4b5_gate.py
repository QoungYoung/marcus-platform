# -*- coding: utf-8 -*-
"""B4/B5 生产接线：p2 gate / switch_builder filter / breakout_context.gate。"""
import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

from app.services import p2_entry_gate as pg  # noqa: E402
import switch_builder as SB  # noqa: E402
import breakout_context as bc  # noqa: E402


def test_gate_block_and_downgrade(monkeypatch):
    monkeypatch.setattr(bc, "b4_b5_cached", lambda th, d: {
        "mainline_ret": -0.01, "bank_ret": 0.01, "gold_ret": 0.02,
        "mainline_ok": False, "hedge_flag": True})
    g = bc.gate("AI/算力/科技", "2026-09-03")
    assert g["action"] == "block" and g["allow"] is False
    monkeypatch.setattr(bc, "b4_b5_cached", lambda th, d: {
        "mainline_ret": -0.01, "bank_ret": -0.01, "gold_ret": -0.01,
        "mainline_ok": False, "hedge_flag": False})
    g = bc.gate("AI/算力/科技", "2026-09-03")
    assert g["action"] == "downgrade" and g["allow"] is True


def test_p2_gate_b4b5_downgrade(monkeypatch):
    monkeypatch.setenv("P2_GATE_MODE", "1")
    monkeypatch.setenv("WOLF_B4B5_GATE", "1")
    monkeypatch.setattr(pg, "_b8_gate", lambda symbol=None, ts_code=None: None)
    monkeypatch.setattr(pg, "_b4b5_gate", lambda symbol=None, ts_code=None: {
        "action": "downgrade", "mainline_ok": False, "hedge_flag": False,
        "reasons": ["B4 false"], "theme": "AI/算力/科技"})
    monkeypatch.setattr(pg, "_log", lambda *a, **k: None)
    out = pg.p2_gate_check(symbol="SH600000", ts_code="600000.SH")
    assert out["multiplier"] <= 0.5
    assert out["b4b5"]["action"] == "downgrade"


def test_p2_gate_b4b5_hard_block(monkeypatch):
    monkeypatch.setenv("P2_GATE_MODE", "1")
    monkeypatch.setenv("WOLF_B4B5_HARD", "1")
    monkeypatch.setattr(pg, "_b8_gate", lambda symbol=None, ts_code=None: None)
    monkeypatch.setattr(pg, "_b4b5_gate", lambda symbol=None, ts_code=None: {
        "action": "block", "mainline_ok": True, "hedge_flag": True,
        "reasons": ["B5 true"], "theme": "AI/算力/科技"})
    monkeypatch.setattr(pg, "_log", lambda *a, **k: None)
    out = pg.p2_gate_check(symbol="SH600000", ts_code="600000.SH")
    assert out["hard_block"] is True


def test_switch_builder_filter(monkeypatch):
    fake = types.SimpleNamespace(
        b4b5_gate_enabled=lambda: True,
        asof_day=lambda: "2026-09-03",
        prev_trade_day=lambda day=None: "2026-09-02",
        gate=lambda th, d: {"action": "block" if th == "A" else "pass"},
    )
    monkeypatch.setitem(sys.modules, "breakout_context", fake)
    buy = {"600000": ("A", "确认"), "600001": ("B", "确认")}
    out, blocked = SB.b4b5_filter(buy)
    assert "600000" not in out and "600001" in out
    assert "A" in blocked


def test_confirm_chain_binary_f3f4():
    """B4/B5 以 binary 传入 confirm_chain：F3/F4 能把“确认”翻成“证伪”。"""
    import random
    import pandas as pd
    import confirm_chain as CC
    random.seed(1)
    base = [120 - i * 0.3 for i in range(60)]
    w = [100, 102, 104, 103, 102, 101, 100, 102, 104, 103, 102, 101, 100, 102, 104, 103, 102, 101, 100]
    c = base + w + [106, 106, 106, 106]
    v = [random.uniform(90, 110) for _ in c]
    v[-4] = 300
    ser, vol = pd.Series(c), pd.Series(v)
    ok = CC.confirm_chain(ser, None, vol, mainline_act=1.0, hedge_act=0.0)
    assert ok["stage"] == "确认"
    f4 = CC.confirm_chain(ser, None, vol, mainline_act=1.0, hedge_act=1.0)
    assert f4["stage"] == "证伪" and f4["signals"].get("F4") is True
    f3 = CC.confirm_chain(ser, None, vol, mainline_act=0.0, hedge_act=0.0)
    assert f3["stage"] == "证伪" and f3["signals"].get("F3") is True


def test_p2_gate_b8_block(monkeypatch):
    monkeypatch.setenv("P2_GATE_MODE", "1")
    monkeypatch.setenv("WOLF_B8_HARD", "1")
    monkeypatch.setattr(pg, "_b8_gate", lambda symbol=None, ts_code=None: {
        "state": "break_confirmed", "action": "block", "reasons": ["跌破确认"]})
    monkeypatch.setattr(pg, "_b4b5_gate", lambda symbol=None, ts_code=None: None)
    monkeypatch.setattr(pg, "_log", lambda *a, **k: None)
    out = pg.p2_gate_check(symbol="SH600000", ts_code="600000.SH")
    assert out["hard_block"] is True and out["b8"]["action"] == "block"


def test_switch_builder_b8_filter(monkeypatch):
    fake = types.SimpleNamespace(
        b8_gate_enabled=lambda: True,
        prev_trade_day=lambda day=None: "2026-09-02",
        b8_gate=lambda code, day: {"action": "block" if code == "600000" else "pass",
                                   "state": "break_confirmed" if code == "600000" else "hold"},
    )
    monkeypatch.setitem(sys.modules, "breakout_context", fake)
    buy = {"600000": ("A", "确认"), "600001": ("B", "确认")}
    out, blocked = SB.b8_filter(buy)
    assert "600000" not in out and "600001" in out and "600000" in blocked

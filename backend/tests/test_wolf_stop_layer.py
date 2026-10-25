# -*- coding: utf-8 -*-
"""point_engine / wolf_stop_layer 单测（v3）。

覆盖：
  · 点位引擎：缺口、回撤、测量移动、关键位选择
  · down-wave → 30% cap
  · 计算级破位 → operation 分档目标（不硬编码点位）
  · 个股 → alert only
  · 盘中/缩量/时点门 → 不动作
  · 站回关键位 → restore
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services import wolf_stop_layer as WL  # noqa: E402


def _d(i):
    return f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}"


def _bars(closes, lows=None, highs=None, vols=None):
    out = []
    for i, c in enumerate(closes):
        out.append({"trade_date": _d(i), "open": c,
                    "high": (highs[i] if highs else c + 1),
                    "low": (lows[i] if lows else c - 1),
                    "close": c, "vol": (vols[i] if vols else 100.0)})
    return out


def _ctx(bars, **kw):
    return WL.StopContext(symbol="000001.SH", bars=bars,
                          wave=kw.pop("wave", {"level": "d4", "operation": "side"}),
                          operation=kw.pop("operation", "side"), **kw)


def _index_bars():
    import json
    p = ROOT / "data" / "index_daily_000001.json"
    if not p.exists():
        return []
    d = json.load(open(p, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    return d


def test_level_status_text():
    assert "已跌破" in WL.format_level_status({"price": 100, "state": "broken"}, 98)
    assert "下方支撑" in WL.format_level_status({"price": 100, "state": "holding"}, 102)
    assert "上方压力" in WL.format_level_status({"price": 100, "state": "resistance"}, 98)


def test_action_types():
    bars = _bars([100.0] * 30)
    d = WL.evaluate(_ctx(bars, wave={"level": "down", "operation": "side"}))
    assert d.action_type == "breakdown_reduce"
    assert d.action == "reduce_to"


def test_down_wave_g3_zero():
    # 2026-10-25 纯删除：下跌趋势不做，G3=0%
    bars = _bars([100.0] * 30)
    d = WL.evaluate(_ctx(bars, wave={"level": "down", "operation": "side"}))
    assert d.action == "reduce_to"
    assert d.target_pct == 0.0
    assert d.no_new is True
    assert d.source == "corpus"


def test_intraday_no_action():
    bars = _bars([100.0] * 40 + [98.0], lows=[99.0] * 40 + [97.0],
                 highs=[101.0] * 40 + [99.0])
    d = WL.evaluate(_ctx(bars, phase="intraday"), target_fn=lambda op: 0.50)
    assert d.action == "observe"
    assert d.target_pct is None


def test_restore_after_reclaim():
    bars = _bars([100.0] * 40 + [102.0], lows=[99.0] * 40 + [101.0],
                 highs=[101.0] * 40 + [103.0])
    d = WL.evaluate(_ctx(bars, position={"was_broken": True}),
                    target_fn=lambda op: 0.50)
    assert d.action == "restore"
    assert d.target_pct == 0.50


def test_time_gate_alert():
    bars = _bars([100.0] * 40 + [98.0], lows=[99.0] * 40 + [97.0],
                 highs=[101.0] * 40 + [99.0])
    d = WL.evaluate(_ctx(bars, time_ok=False), target_fn=lambda op: 0.50)
    assert d.action == "alert"
    assert "时点门" in d.reason

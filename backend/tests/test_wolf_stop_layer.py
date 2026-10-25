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

from app.services import point_engine as PE  # noqa: E402
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


def test_point_engine_real_cases():
    """用他点过的历史点位做外部校验：只要引擎能算出附近同类位，不是硬编码。"""
    bars = _index_bars()
    if not bars:
        return
    cases = [
        ("20251205", [3922, 3931, 3888, 3850]),
        ("20250620", [3373, 3334, 3407]),
        ("20260814", [4000, 3888, 3966, 3903]),
        ("20251016", [3674, 3731]),
        ("20260121", [4100, 4141]),
    ]
    for day, targets in cases:
        sub = [b for b in bars if str(b["trade_date"]) <= day]
        res = PE.compute_points(sub, asof=day, domain="index", symbol="000001.SH")
        for t in targets:
            hit = any(abs(p["price"] - t) / t * 100 <= 0.6 for p in res["points"])
            assert hit, "%s 应算出附近的 %s；top=%s" % (
                day, t, [(p["type"], p["price"]) for p in res["points"][:8]])


def test_point_engine_gap():
    bars = _bars([100.0] * 30 + [106.0],
                 lows=[99.0] * 30 + [105.0],
                 highs=[101.0] * 30 + [107.0])
    res = PE.compute_points(bars, asof=bars[-1]["trade_date"])
    gaps = [p for p in res["points"] if p["type"] == "gap_lower"]
    assert gaps, "应识别向上缺口下沿"
    assert any(abs(p["price"] - 101.0) < 1.0 for p in gaps)


def test_point_engine_retracement():
    closes = [100.0] * 10 + [110, 120, 130, 140, 150, 160, 170, 180, 190, 200] + [180.0] * 5
    bars = _bars(closes, lows=[c - 1 for c in closes], highs=[c + 1 for c in closes])
    res = PE.compute_points(bars, asof=bars[-1]["trade_date"])
    rs = [p for p in res["points"] if p["type"] == "retracement"]
    assert rs, "应识别 0.382/0.5/0.618 回撤"
    assert any(abs(p["meta"]["pct"] - 0.382) < 1e-6 for p in rs)


def test_point_engine_measured_move():
    closes = [100.0] * 10 + [105, 110, 115, 120] + [110.0] * 10
    bars = _bars(closes, lows=[c - 1 for c in closes], highs=[c + 1 for c in closes])
    res = PE.compute_points(bars, asof=bars[-1]["trade_date"])
    mm = [p for p in res["points"] if p["type"] in ("measured_move", "range_symmetry")]
    assert mm, "应识别测量移动/对称投影"


def test_point_engine_today_high_ma20_round():
    """2025-06-20 的 3370 必须作为 today_high/ma20/round10 出现，不能被 neckline 合并掉。"""
    bars = _index_bars()
    if not bars:
        return
    sub = [b for b in bars if str(b["trade_date"]) <= "20250620"]
    res = PE.compute_points(sub, asof="20250620", domain="index", symbol="000001.SH")
    near = [p for p in res["points"] if abs(p["price"] - 3370) / 3370 <= 0.003]
    types = {p["type"] for p in near}
    assert "today_high" in types
    assert "ma20" in types
    assert "round10" in types


def test_level_status_text():
    assert "已跌破" in WL.format_level_status({"price": 100, "state": "broken"}, 98)
    assert "下方支撑" in WL.format_level_status({"price": 100, "state": "holding"}, 102)
    assert "上方压力" in WL.format_level_status({"price": 100, "state": "resistance"}, 98)


def test_action_types():
    bars = _bars([100.0] * 30)
    d = WL.evaluate(_ctx(bars, wave={"level": "down", "operation": "side"}))
    assert d.action_type == "breakdown_reduce"
    assert d.action == "reduce_to"


def test_key_levels():
    bars = _bars([100.0] * 40, lows=[99.0] * 40, highs=[101.0] * 40)
    res = PE.compute_points(bars, asof=bars[-1]["trade_date"])
    keys = PE.key_levels(res, n=3)
    assert keys, "应选出关键位"
    assert all(k["price"] <= res["close"] * 1.005 for k in keys)


def test_down_wave_g3_15():
    # 2026-10-25 拍板：G3 = 底仓10 + T5 = 15%，不再写死 30
    bars = _bars([100.0] * 30)
    d = WL.evaluate(_ctx(bars, wave={"level": "down", "operation": "side"}))
    assert d.action == "reduce_to"
    assert d.target_pct == 0.15
    assert d.no_new is True
    assert d.source == "corpus"


def test_computed_break_tier_target():
    bars = _bars([100.0] * 40 + [98.0], lows=[99.0] * 40 + [97.0],
                 highs=[101.0] * 40 + [99.0], vols=[100.0] * 40 + [200.0])
    d = WL.evaluate(_ctx(bars, operation="side"), target_fn=lambda op: 0.50)
    assert d.action == "reduce_to"
    assert d.target_pct == 0.50
    assert d.layer == "index"
    assert d.level is not None


def test_intraday_no_action():
    bars = _bars([100.0] * 40 + [98.0], lows=[99.0] * 40 + [97.0],
                 highs=[101.0] * 40 + [99.0])
    d = WL.evaluate(_ctx(bars, phase="intraday"), target_fn=lambda op: 0.50)
    assert d.action == "observe"
    assert d.target_pct is None


def test_shrink_break_alerts():
    bars = _bars([100.0] * 40 + [98.0], lows=[99.0] * 40 + [97.0],
                 highs=[101.0] * 40 + [99.0], vols=[100.0] * 40 + [50.0])
    d = WL.evaluate(_ctx(bars), target_fn=lambda op: 0.50)
    assert d.action == "alert"
    assert d.target_pct is None


def test_stock_alert_only():
    idx = _bars([100.0] * 30)
    st = _bars([100.0] * 40 + [98.0], lows=[99.0] * 40 + [97.0],
               highs=[101.0] * 40 + [99.0])
    d = WL.evaluate(_ctx(idx, stock_bars=st, stock_symbol="SZ000001"),
                    target_fn=lambda op: 0.50)
    assert d.action == "alert"
    assert d.layer == "stock"
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

# -*- coding: utf-8 -*-
"""break_detector 单测（BreakStop v1 / P2）。

覆盖：
  · 盘中破位只 B0、永不确认
  · 收盘破位 + 放量 → confirmed
  · 收盘破位 + 缩量 → shrink_break；反抽失败 → rebound_fail；站回 → reclaimed
  · 3×15min 不收回 → confirmed
  · 缓冲带 / 收盘在 level 上方 → 不确认
  · PIT：未来 bar 不改变历史信号
  · 真实指数判例：2026-02-05 前低持有、2025-12-11 前低跌破
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services import break_detector as BD  # noqa: E402

INDEX_JSON = ROOT / "data" / "index_daily_000001.json"


def _date(i):
    return f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}"


def _bars(closes, vols=None, highs=None, lows=None):
    out = []
    for i, c in enumerate(closes):
        out.append({
            "trade_date": _date(i),
            "open": c, "high": (highs[i] if highs else c + 0.5),
            "low": (lows[i] if lows else c - 0.5),
            "close": c, "vol": (vols[i] if vols else 100.0),
            "amount": 10000.0,
        })
    return out


def _level(price=100.0, domain="stock", typ="platform_low_20"):
    return {"level_id": f"X:{typ}:20260101:1", "symbol": "SZ000001",
            "domain": domain, "type": typ, "price": price, "priority": 60}


def _run(closes, vols=None, highs=None, lows=None, **cfg):
    bars = _bars(closes, vols, highs, lows)
    return BD.run_series(_level(), bars, config=cfg or None)


def test_intraday_break_only():
    bars = _bars([101, 100.5, 99], vols=[100, 100, 100],
                 lows=[100.8, 100.2, 98.5])
    level = _level()
    sig = BD.detect(level, bars, phase="intraday", quote_price=99.2)
    assert sig["status"] == "intraday_break"
    assert sig["confirmed"] is False
    assert sig["phase"] == "intraday"


def test_close_break_volume_confirmed():
    sigs = _run([101] * 7 + [100.5, 100.2, 99],
                vols=[100] * 9 + [200])
    last = sigs[-1]
    assert last["status"] == "confirmed"
    assert last["vol_ratio"] >= 1.2
    assert last["volume_branch"] == "strong"
    assert last["confirmed"] is True


def test_shrink_break_then_reclaimed():
    sigs = _run([101] * 7 + [100.5, 99, 101],
                vols=[100] * 7 + [100, 50, 100])
    assert sigs[8]["status"] == "shrink_break"
    assert sigs[9]["status"] == "reclaimed"
    assert sigs[9]["confirmed"] is False


def test_shrink_break_then_rebound_fail():
    sigs = _run([101] * 7 + [100.5, 99, 99.5],
                vols=[100] * 7 + [100, 50, 60],
                highs=[100.5] * 7 + [100.5, 99.5, 100.2])
    assert sigs[8]["status"] == "shrink_break"
    assert sigs[9]["status"] == "rebound_fail"
    assert sigs[9]["confirmed"] is True


def test_m15_no_reclaim_confirms():
    bars = _bars([101] * 7 + [100.5, 99, 99.4], vols=[100] * 10)
    m15 = {bars[-1]["trade_date"]: [
        {"time": "14:30", "close": 99.8},
        {"time": "14:45", "close": 99.7},
        {"time": "15:00", "close": 99.6},
    ]}
    sigs = BD.run_series(_level(), bars, m15_by_date=m15)
    last = sigs[-1]
    assert last["m15_no_reclaim"] is True
    assert last["status"] == "confirmed"


def test_c9_prior_high_2x_volume_confirms():
    """2026-05-21「放量跌破昨天的红 K 量能多一倍」：prior_day_high 支撑 + 2× 量 → confirmed。"""
    bars = _bars([101] * 7 + [100.5, 99.8, 99.0],
                 vols=[100] * 9 + [200],
                 highs=[101.5] * 7 + [101.0, 100.2, 99.5])
    lv = _level(price=100.5, typ="prior_day_high")
    sigs = BD.run_series(lv, bars)
    last = sigs[-1]
    assert last["vol_strong"] is True
    assert last["status"] == "confirmed"

def test_level_above_never_crossed_is_not_break():
    """压力位在现价上方、从未站上过 → 不能算破位（防止 round_number 110 误报）。"""
    bars = _bars([101.0] * 20)
    lv = _level(price=110.0, typ="round_number")
    sigs = BD.run_series(lv, bars)
    assert sigs[-1]["status"] in ("none", "watch")
    assert sigs[-1]["confirmed"] is False


def test_level_above_after_crossing_can_break():
    """曾站上 110（收盘 111）后跌回 101 → 才允许算破位。"""
    bars = _bars([111.0] * 8 + [101.0] * 3, vols=[100.0] * 10 + [200.0])
    lv = _level(price=110.0, typ="round_number")
    sigs = BD.run_series(lv, bars)
    assert sigs[-1]["status"] == "confirmed"


def test_seen_above_can_be_disabled():
    """`require_seen_above` 是工程护栏，可关；关掉后压力位也会被判破位（用于对比实验）。"""
    bars = _bars([101.0] * 20)
    lv = _level(price=110.0, typ="round_number")
    sigs = BD.run_series(lv, bars, config={"require_seen_above": False})
    assert sigs[-1]["status"] in ("close_break", "confirmed", "shrink_break")


def test_close_above_is_none():
    sigs = _run([101] * 10)
    assert sigs[-1]["status"] == "none"
    assert sigs[-1]["confirmed"] is False


def test_buffer_zone_is_watch():
    sigs = _run([101] * 7 + [100.5, 99.9], lows=[100.5] * 7 + [100.4, 99.5],
                vols=[100] * 9)
    last = sigs[-1]
    assert last["status"] == "watch"
    assert last["confirmed"] is False


def test_pit_series_no_future():
    closes = [101] * 7 + [100.5, 99, 101]
    vols = [100] * 7 + [100, 50, 100]
    full = BD.run_series(_level(), _bars(closes, vols))
    part = BD.run_series(_level(), _bars(closes[:9], vols[:9]))
    assert [s["status"] for s in full[:9]] == [s["status"] for s in part]
    assert [s["close"] for s in full[:9]] == [s["close"] for s in part]


def _index_bars():
    if not INDEX_JSON.exists():
        pytest.skip("index_daily_000001.json 不存在")
    d = json.load(open(INDEX_JSON, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    return d


def test_index_c11_prior_low_hold():
    """2026-02-05 收盘 4075.92 未跌破前一日低点 4062.68 → 不确认。"""
    bars = [b for b in _index_bars() if str(b["trade_date"]) <= "20260205"]
    lv = {"level_id": "000001.SH:prior_day_low:20260205:20260204",
          "symbol": "000001.SH", "domain": "index", "type": "prior_day_low",
          "price": 4062.68, "priority": 65}
    sigs = BD.run_series(lv, bars)
    last = sigs[-1]
    assert last["close"] > 4062.68
    assert last["status"] == "none"
    assert last["confirmed"] is False


def test_index_c12_prior_low_daily_is_watch():
    """2025-12-11 收盘 3873.32 只比前低 3876.47 低 0.08%，未破 0.3% 缓冲带：
    日线口径只能 watch；他的“3 个 15min 不收回”才是确认条件。"""
    bars = [b for b in _index_bars() if str(b["trade_date"]) <= "20251211"]
    lv = {"level_id": "000001.SH:prior_day_low:20251211:20251210",
          "symbol": "000001.SH", "domain": "index", "type": "prior_day_low",
          "price": 3876.47, "priority": 65}
    sigs = BD.run_series(lv, bars)
    last = sigs[-1]
    assert last["close"] < 3876.47
    assert last["below"] is True
    assert last["below_buffer"] is False
    assert last["status"] == "watch"
    assert last["confirmed"] is False


def test_index_c12_prior_low_with_m15_confirms():
    """同日给 3 根 15min 收盘都在 3876.47 下方 → 越过缓冲带确认（放量/平量，非缩量）。"""
    bars = [b for b in _index_bars() if str(b["trade_date"]) <= "20251211"]
    lv = {"level_id": "000001.SH:prior_day_low:20251211:20251210",
          "symbol": "000001.SH", "domain": "index", "type": "prior_day_low",
          "price": 3876.47, "priority": 65}
    m15 = {"20251211": [
        {"time": "14:30", "close": 3873.0},
        {"time": "14:45", "close": 3872.5},
        {"time": "15:00", "close": 3871.8},
    ]}
    sigs = BD.run_series(lv, bars, m15_by_date=m15)
    last = sigs[-1]
    assert last["m15_no_reclaim"] is True
    assert last["status"] == "confirmed"
    assert last["confirmed"] is True

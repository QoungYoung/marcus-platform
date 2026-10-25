# -*- coding: utf-8 -*-
"""break_levels 单测（BreakStop v1 / P1）。

覆盖：
  · 平台低 / 均线 / BOLL / 整数关 / 趋势线 / 缺口 / 摆动点 / 锚点
  · PIT：asof 之后追加数据不改变当日 level
  · 真实指数判例：2025-11-21 的 3922、2025-12-03 的 3888/3850、2026-01-28 的 3764 锚点
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services import break_levels as BL  # noqa: E402

INDEX_JSON = ROOT / "data" / "index_daily_000001.json"


def _bars(n=80, start=10.0, step=0.1, gap_at=None):
    out = []
    for i in range(n):
        close = start + i * step
        high = close + 0.05
        low = close - 0.05
        if gap_at is not None and i == gap_at:
            low = close + 0.5  # 向上跳空
        out.append({
            "trade_date": f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
            "open": close, "high": high, "low": low, "close": close,
            "vol": 1000.0, "amount": 10000.0,
        })
    return out


def _index_bars():
    if not INDEX_JSON.exists():
        pytest.skip("index_daily_000001.json 不存在")
    d = json.load(open(INDEX_JSON, encoding="utf-8"))
    d.sort(key=lambda r: str(r["trade_date"]))
    return d


def _by_type(res, typ):
    return [lv for lv in res["levels"] if lv["type"] == typ]


def test_platform_ma_boll():
    res = BL.compute_levels(_bars(), domain="stock", symbol="SZ000001")
    assert res["data_quality"]["insufficient"] is False
    assert _by_type(res, "platform_low_20")
    assert _by_type(res, "platform_low_60")
    assert _by_type(res, "ma13")
    assert _by_type(res, "ma34")
    assert _by_type(res, "ma60")
    assert _by_type(res, "boll_mid")
    # 平台低 = 最近 20 根最低 low
    lows = [b["low"] for b in _bars()[-20:]]
    assert abs(_by_type(res, "platform_low_20")[0]["price"] - min(lows)) < 1e-6


def test_pit_no_future():
    bars = _bars(90)
    asof = bars[59]["trade_date"]
    a = BL.compute_levels(bars[:60], asof=asof, domain="stock", symbol="SZ000001")
    b = BL.compute_levels(bars, asof=asof, domain="stock", symbol="SZ000001")
    # 只看 asof 之前的 level；未来 bar 不得改变
    pa = sorted((lv["type"], lv["price"]) for lv in a["levels"])
    pb = sorted((lv["type"], lv["price"]) for lv in b["levels"])
    assert pa == pb
    assert b["asof"] == asof


def test_round_numbers():
    res = BL.compute_levels(_bars(n=30, start=3921.0, step=0.0), domain="index",
                            symbol="000001.SH", config={"platform_windows": (20,),
                                                        "swing_k": 3})
    rounds = {lv["price"] for lv in _by_type(res, "round_number")}
    assert 3900.0 in rounds
    assert 3950.0 in rounds


def test_trendline_downtrend():
    bars = _bars(n=60, start=100.0, step=-1.0)
    res = BL.compute_levels(bars, domain="stock", symbol="SZ000001")
    tl = _by_type(res, "trendline")
    assert tl, "下降趋势应产生趋势线 level"
    assert any(lv["meta"]["slope"] < 0 for lv in tl)


def test_gap_lower():
    bars = _bars(n=60, start=10.0, step=0.1, gap_at=40)
    res = BL.compute_levels(bars, domain="stock", symbol="SZ000001",
                            config={"gap_lookback": 60, "gap_min_pct": 0.001})
    gaps = _by_type(res, "gap_lower")
    assert gaps, "向上跳空应产生 gap_lower"
    assert gaps[0]["price"] > 0


def test_prior_day_low():
    bars = _bars()
    res = BL.compute_levels(bars, domain="stock", symbol="SZ000001")
    lv = _by_type(res, "prior_day_low")
    assert lv and abs(lv[0]["price"] - bars[-1]["low"]) < 1e-9
    assert lv[0]["anchor"] == bars[-1]["trade_date"]


def test_prior_day_high_still_present():
    bars = _bars()
    res = BL.compute_levels(bars, domain="stock", symbol="SZ000001")
    lv = _by_type(res, "prior_day_high")
    assert lv and abs(lv[0]["price"] - bars[-1]["high"]) < 1e-9


def test_no_platform_high_as_stop_level():
    """平台高点/箱体上沿不是他的止损 level，P1 不应产出。"""
    res = BL.compute_levels(_bars(), domain="stock", symbol="SZ000001")
    types = {lv["type"] for lv in res["levels"]}
    assert "platform_high" not in types
    assert "box_upper" not in types
    assert "box_low" not in types


def test_swing_low_minus_3():
    res = BL.compute_levels(_bars(), domain="stock", symbol="SZ000001",
                            config={"entry_anchor": 10.0})
    lv = _by_type(res, "swing_low_minus_3")
    assert lv and abs(lv[0]["price"] - 9.7) < 1e-6


def test_wave_anchor():
    res = BL.compute_levels(_bars(), domain="index", symbol="000001.SH",
                            anchors=[{"type": "wave_anchor", "price": 3764.15,
                                      "source": "wave_state"}])
    lv = _by_type(res, "wave_anchor")
    assert lv and abs(lv[0]["price"] - 3764.15) < 1e-6


def test_insufficient_data():
    res = BL.compute_levels(_bars(n=5), domain="stock", symbol="SZ000001")
    assert res["data_quality"]["insufficient"] is True
    assert "bars_lt_20" in res["data_quality"]["warnings"]


def test_level_id_semantic():
    res = BL.compute_levels(_bars(), domain="stock", symbol="SZ000001")
    for lv in res["levels"]:
        assert ":" in lv["level_id"]
        assert lv["level_id"].startswith("SZ000001:")


def test_index_case_20251121():
    bars = _index_bars()
    res = BL.compute_levels(bars, asof="20251120", domain="index", symbol="000001.SH")
    pl = _by_type(res, "platform_low_20")
    assert pl, "2025-11-20 应有 20 日平台低"
    # 他说的 3922（2025-11-21）
    assert abs(pl[0]["price"] - 3922.58) / 3922.58 < 0.01
    ma5 = _by_type(res, "ma5")
    assert ma5 and abs(ma5[0]["price"] - 3956.02) / 3956.02 < 0.01
    sup = BL.nearest_support(res)
    assert sup and abs(sup["price"] - 3922.58) / 3922.58 < 0.01


def test_index_case_20251203():
    bars = _index_bars()
    res = BL.compute_levels(bars, asof="20251202", domain="index", symbol="000001.SH")
    ma5 = _by_type(res, "ma5")
    assert ma5 and abs(ma5[0]["price"] - 3887.95) / 3887.95 < 0.01
    rounds = {lv["price"] for lv in _by_type(res, "round_number")}
    assert 3850.0 in rounds


def test_index_case_20260205_prior_low():
    """2026-02-05「只要当天收盘没有跌破前一天低点 都是70%仓位」：
    asof=2026-02-04，prior_day_low = 4062.68；02-05 收盘 4075.92 在其上方 → 持有。"""
    bars = _index_bars()
    res = BL.compute_levels(bars, asof="20260204", domain="index", symbol="000001.SH")
    lv = _by_type(res, "prior_day_low")
    assert lv and abs(lv[0]["price"] - 4062.68) < 0.01
    # 次日收盘未跌破
    nxt = [b for b in bars if str(b["trade_date"]) == "20260205"][0]
    assert float(nxt["close"]) > lv[0]["price"]


def test_index_case_3764_anchor():
    bars = _index_bars()
    res = BL.compute_levels(bars, asof="20260128", domain="index", symbol="000001.SH",
                            anchors=[{"type": "wave_anchor", "price": 3764.15,
                                      "source": "wave_state"}])
    lv = _by_type(res, "wave_anchor")
    assert lv and abs(lv[0]["price"] - 3764.15) < 1e-6

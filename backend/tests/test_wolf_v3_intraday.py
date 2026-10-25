# -*- coding: utf-8 -*-
from datetime import datetime
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
from app.services import wolf_v3_intraday as D  # noqa: E402


def _bars(closes):
    out = []
    h, m = 9, 35
    for i, c in enumerate(closes):
        out.append({"trade_time": "2026-01-14 %02d:%02d" % (h, m), "open": c, "high": c + 1,
                    "low": c - 1, "close": c, "vol": 100})
        m += 5
        if m >= 60:
            h, m = h + 1, m - 60
    return out


def test_3x15_confirmed():
    # 3 个 15min slot：09:30-09:45 / 09:45-10:00 / 10:00-10:15
    bars = _bars([99, 99, 99, 99, 99, 99, 99, 99, 99])
    r = D.detect_3x15(bars, 100, now=datetime(2026, 1, 14, 10, 16))
    assert r["confirmed"] is True
    assert r["bar_count"] >= 3


def test_3x15_not_confirmed_incomplete():
    bars = _bars([99, 99, 99, 99, 99, 99, 99, 99, 99])
    r = D.detect_3x15(bars, 100, now=datetime(2026, 1, 14, 10, 10))
    assert r["confirmed"] is False


def test_volume_break():
    bars = _bars([101, 101, 101, 99])
    bars[-1]["vol"] = 300
    r = D.detect_volume_break(bars, 100, now=datetime(2026, 1, 14, 9, 55), ratio_threshold=1.5)
    assert r["confirmed"] is True
    assert r["volume_ratio"] >= 1.5

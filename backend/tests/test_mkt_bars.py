# -*- coding: utf-8 -*-
"""回测行情表 mkt_bars（2026-09-12）单测：字段映射与 NaN 过滤（纯函数，不联网/不连库）。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import mkt_bars as MB  # noqa: E402


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)
    def __getattr__(self, item):
        return None


class _DF:
    def __init__(self, rows):
        self._rows = rows
        self.columns = list(rows[0].__dict__.keys()) if rows else []
    def __len__(self):
        return len(self._rows)
    def itertuples(self, index=False):
        return iter(self._rows)
    def __getitem__(self, k):
        return [getattr(r, k) for r in self._rows]


def test_f_filters_nan_and_bad():
    assert MB._f(float("nan")) is None
    assert MB._f("abc") is None
    assert MB._f(None) is None
    assert MB._f("1.5") == 1.5


def test_day_rows_maps_fields_and_marks_st(monkeypatch):
    daily = _DF([_Row(ts_code="600519.SH", open=1.0, high=2.0, low=0.5, close=1.5,
                      pre_close=1.4, pct_chg=7.14, vol=100.0, amount=200.0)])
    basic = _DF([_Row(ts_code="600519.SH", total_mv=1234.5, turnover_rate=3.2)])

    class _Pro:
        def daily(self, trade_date=None):
            return daily
        def daily_basic(self, trade_date=None, fields=None):
            return basic
    monkeypatch.setattr(MB, "_pro", lambda: _Pro())
    rows = MB.day_rows("20260911", {"600519.SH": False})
    assert len(rows) == 1
    r = rows[0]
    assert r["ts_code"] == "600519.SH" and r["trade_date"] == "20260911"
    assert r["pct_chg"] == 7.14 and r["pre_close"] == 1.4 and r["amount"] == 200.0
    assert r["total_mv"] == 1234.5 and r["turnover_rate"] == 3.2 and r["is_st"] is False


def test_day_rows_tolerates_missing_basic(monkeypatch):
    daily = _DF([_Row(ts_code="000001.SZ", open=1.0, high=1.0, low=1.0, close=1.0,
                      pre_close=1.0, pct_chg=0.0, vol=1.0, amount=1.0)])

    class _Pro:
        def daily(self, trade_date=None):
            return daily
        def daily_basic(self, trade_date=None, fields=None):
            raise RuntimeError("rate limited")
    monkeypatch.setattr(MB, "_pro", lambda: _Pro())
    rows = MB.day_rows("20260911", None)
    assert len(rows) == 1 and rows[0]["total_mv"] is None and rows[0]["is_st"] is None


def test_day_rows_empty(monkeypatch):
    class _Pro:
        def daily(self, trade_date=None):
            return _DF([])
    monkeypatch.setattr(MB, "_pro", lambda: _Pro())
    assert MB.day_rows("20260911", {}) == []

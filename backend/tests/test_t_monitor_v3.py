# -*- coding: utf-8 -*-
"""t_monitor v3 灰度接线测试（2026-10-23）。

只测接线，不测行情：
  · STOP_V3 旁路旧腿型
  · _check_stop_loss 在 STOP_V3 下只调 v3 记录，不走旧止损
  · _v3_context 把当日 quote 拼成 bar
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _d in (ROOT, ROOT / "backend"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_monitor as TM  # noqa: E402


def test_v3_bypass_kinds():
    assert "stop_loss" in TM._V3_BYPASS_KINDS
    assert "custom_support_sell" in TM._V3_BYPASS_KINDS
    assert "wolf_passive_stop_sell" in TM._V3_BYPASS_KINDS
    assert "custom_vwap_sell" not in TM._V3_BYPASS_KINDS  # T 腿保留


def test_v3_scans_positions_without_conditions():
    m = object.__new__(TM.TMonitor)
    m._positions = lambda account=None: [{"symbol": "SZ000001", "volume": 100}]
    m._fetch_quotes_concurrent = lambda syms: {"sz000001": {"current": 10.0}}
    m._check_stop_loss_v3 = lambda s, q, l: m.__dict__.setdefault("seen", []).append(s)
    with mock.patch("app.services.t_gateway.get_sellable_ledger",
                    lambda *a, **k: {"SZ000001": {"sellable": 100}}):
        m._check_all_positions_v3()
    assert m.seen == ["SZ000001"]


def test_v3_context_appends_today_quote():
    m = object.__new__(TM.TMonitor)
    m._daily_dated = lambda sym, n=40: [
        {"date": "20260101", "close": 10.0, "high": 10.2, "low": 9.8, "vol": 100},
        {"date": "20260102", "close": 10.1, "high": 10.3, "low": 9.9, "vol": 110},
    ]
    m._v3_index_bars = lambda: [
        {"trade_date": "20260101", "close": 3000, "high": 3010, "low": 2990, "vol": 1},
        {"trade_date": "20260102", "close": 3010, "high": 3020, "low": 3000, "vol": 1},
    ]
    quote = {"current": 10.5, "open": 10.1, "high": 10.6, "low": 10.0, "pre_close": 10.1}
    with mock.patch.object(TM, "_in_close_window", lambda: True), \
         mock.patch.object(TM, "_stop_time_ok", lambda: (True, "")), \
         mock.patch("app.services.wave_store.get_wave",
                    lambda: {"level": "d4", "operation": "side"}):
        ctx = m._v3_context("SZ000001", quote, {"SZ000001": {"sellable": 100, "avg_price": 10.0}})
    assert ctx.phase == "close"
    assert ctx.operation == "side"
    assert ctx.stock_bars[-1]["trade_date"] == TM.datetime.now().strftime("%Y%m%d")
    assert abs(ctx.stock_bars[-1]["close"] - 10.5) < 1e-9
    assert abs(ctx.position["pnl_pct"] - 5.0) < 1e-6


def test_v3_log_is_noop_on_bad_path(tmp_path):
    m = object.__new__(TM.TMonitor)
    with mock.patch.dict("os.environ", {"DATA_DIR": str(tmp_path)}):
        m._v3_log({"x": 1})
    p = tmp_path / "stop_v3_events.jsonl"
    assert p.exists() and "x" in p.read_text(encoding="utf-8")

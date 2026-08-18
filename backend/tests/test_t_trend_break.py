# -*- coding: utf-8 -*-
"""t-trend-breakout-short-term 单元测试（核心为 monkeypatch 纯逻辑，不依赖 DB）。"""
import unittest
from unittest import mock

from app.services import t_trend_break as tb


class TestTrendBreakPure(unittest.TestCase):
    """账户隔离与日频入池纯逻辑（不依赖 DB）。"""

    def test_account_isolation_constant(self):
        self.assertEqual(tb._account_id(), "t")

    def test_normalize(self):
        self.assertEqual(tb._normalize("002384"), "SZ002384")
        self.assertEqual(tb._normalize("600519"), "SH600519")
        self.assertEqual(tb._normalize("SH600519"), "SH600519")

    @mock.patch.object(tb, "fetch_moneyflow_main_net", return_value=(100.0, 500.0))
    @mock.patch.object(tb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(tb, "fetch_daily_bars")
    def test_day_filter_breakout_pass(self, bars, mcap, mf):
        """放量突破 + MA20 转上 + 资金正 + 小市值 -> 入池。"""
        seq = []
        for i in range(34):
            close = 10.0 + (i - 10) * 0.5 if i < 10 else 10.0 + (i - 10) * 0.6
            vol = 1000.0 if i < 33 else 4000.0
            high = close + 0.2 if i < 33 else close + 0.5
            seq.append({"date": "20260101", "open": close, "high": high,
                        "low": close - 0.1, "close": close, "vol": vol})
        bars.return_value = seq
        ok, reasons, score = tb.day_filter("002384")
        self.assertTrue(ok, reasons)
        self.assertGreater(score, 0)

    @mock.patch.object(tb, "fetch_moneyflow_main_net", return_value=(100.0, 500.0))
    @mock.patch.object(tb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(tb, "fetch_daily_bars")
    def test_day_filter_no_breakout_reject(self, bars, mcap, mf):
        """未突破前高 -> 拒绝。"""
        seq = [{"date": "20260101", "open": 10.0, "high": 10.5, "low": 9.9,
                "close": 10.0, "vol": 1000.0} for _ in range(34)]
        seq[-1]["high"] = 10.2
        bars.return_value = seq
        ok, reasons, _ = tb.day_filter("002384")
        self.assertFalse(ok)
        self.assertTrue(any("未突破" in r for r in reasons))

    def test_trading_days_since(self):
        self.assertGreaterEqual(tb._trading_days_since("2026-08-01"), 0)


class TestTrendBreakBuildSizing(unittest.TestCase):
    """trend_break 独立规模档（monkeypatch 掉 DB 读取）。"""

    def setUp(self):
        from app.services import t_build
        self.t_build = t_build

    def test_trend_break_sizing_independent(self):
        tb_ = self.t_build
        with mock.patch.object(tb_, "_params", return_value=dict(tb_.BUILD_PARAMS_DEFAULT)), \
             mock.patch.object(tb_, "t_net_asset", return_value=250000.0), \
             mock.patch.object(tb_, "_positions_value", return_value=(0.0, {})), \
             mock.patch.object(tb_, "get_sellable_ledger", return_value={}):
            s = tb_.build_sizing("SZ002384", 10.0, mode="trend_break")
            self.assertAlmostEqual(s["single_max_amount"], 75000.0, delta=1)   # 30% x 25万
            self.assertAlmostEqual(s["total_floor_max"], 150000.0, delta=1)    # 60% x 25万
            s2 = tb_.build_sizing("SZ002384", 10.0, mode="standard")
            self.assertAlmostEqual(s2["single_max_amount"], 25000.0, delta=1)  # std 10% x 25万


class TestTrendBreakBuildWiring(unittest.TestCase):
    """build_t_position trend_break 模式接线（mock 网关，无 DB）。"""

    def test_trend_break_skips_timing_and_passes_mode(self):
        from app.services import t_build
        calls = {}

        def fake_gw(symbol, price, volume, reason="", decision_source="agent",
                    event_id=None, force_human=False, build_mode="standard"):
            calls["build_mode"] = build_mode
            calls["decision_source"] = decision_source
            return {"status": "success", "price": price, "volume": volume}

        with mock.patch.object(t_build, "build_gateway_execute", side_effect=fake_gw), \
             mock.patch.object(t_build, "confirm_build_timing",
                               side_effect=AssertionError("trend_break 不应走回踩时机确认")):
            out = t_build.build_t_position("SZ002384", 10.0, volume=100,
                                           reason="trend_break 测试",
                                           decision_source="ai_led",
                                           build_mode="trend_break")
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(calls.get("build_mode"), "trend_break")
        self.assertEqual(calls.get("decision_source"), "ai_led")






class TestTrendBreakExits(unittest.TestCase):
    """短线出场：+5% 减半 / +8% 清 / -5% 硬止损 / 5交易日超时（mock 持仓与网关）。"""

    def _run(self, pnl_pct, volume=100, built="2026-08-01"):
        with mock.patch.object(tb, "_trend_break_positions",
                               return_value=[{"symbol": "SZ002384", "volume": volume,
                                              "avg_price": 10.0, "built_at": built}]),              mock.patch.object(tb, "fetch_realtime", return_value={"price": 10.0 * (1 + pnl_pct)}),              mock.patch("app.services.t_gateway.gateway_execute", return_value={"status": "success"}),              mock.patch.object(tb, "_trading_days_since", return_value=10 if built == "2026-08-10" else 2):
            return tb.check_exits()

    def test_stop_loss_sells_all(self):
        res = self._run(-0.06)
        self.assertEqual(len(res), 1)
        self.assertIn("止损", res[0]["reason"])

    def test_tp8_sells_all(self):
        res = self._run(0.09)
        self.assertEqual(len(res), 1)
        self.assertIn("+8%", res[0]["reason"])

    def test_tp5_sells_half(self):
        res = self._run(0.06, volume=200)
        self.assertEqual(len(res), 1)
        self.assertIn("减半", res[0]["reason"])
        self.assertEqual(res[0]["volume"], 100)

    def test_timeout_sells_all(self):
        res = self._run(0.01, built="2026-08-10")
        self.assertEqual(len(res), 1)
        self.assertIn("超时", res[0]["reason"])


class TestTrendBreakRealtimeDegrade(unittest.TestCase):
    """实时数据不可用 -> 降级等待，不盲买（不连坐建仓）。"""

    def test_realtime_unavailable_defers_build(self):
        with mock.patch.object(tb, "_pending_candidates",
                               return_value=[{"id": 1, "symbol": "SZ002384", "score": 1.0}]),              mock.patch.object(tb, "fetch_realtime", return_value=None),              mock.patch.object(tb, "_mark_candidate") as mk,              mock.patch("app.services.t_build.build_t_position") as bld:
            res = tb.try_build_candidates()
        self.assertEqual(res[0]["status"], "wait_realtime")
        bld.assert_not_called()


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""t-vreb-etf 科技ETF V反短线 单元测试（monkeypatch 纯逻辑）。"""
import unittest
from unittest import mock

from app.services import t_vreb_etf as ve


def _etf_bars():
    """构造满足 ETF 版 V反（REB>=12%、J>=85/RSI6>=62、bias20<=3%、放行分支）的 70 根日线：
    前 60 根平盘 1.20 -> 6 根急跌至 1.02 -> 4 根反弹至 1.16（+13.7%），量恒 1000（volr=1.0）。
    """
    seq = []
    for i in range(60):
        seq.append({"date": "20260101", "open": 1.20, "high": 1.21, "low": 1.19,
                    "close": 1.20, "vol": 1000.0})
    for i in range(6):
        close = 1.20 - 0.03 * (i + 1)
        seq.append({"date": "20260101", "open": close + 0.005, "high": close + 0.01,
                    "low": close - 0.01, "close": close, "vol": 1000.0})
    for c in (1.05, 1.08, 1.12, 1.16):
        seq.append({"date": "20260101", "open": c - 0.005, "high": c + 0.01,
                    "low": c - 0.005, "close": c, "vol": 1000.0})
    return seq


class TestVrebEtfPure(unittest.TestCase):

    def test_account_isolation(self):
        self.assertEqual(ve._account_id(), "t")

    def test_normalize(self):
        self.assertEqual(ve._normalize("512760"), "SH512760")
        self.assertEqual(ve._normalize("159915"), "SZ159915")
        self.assertEqual(ve._normalize("SH512760"), "SH512760")

    @mock.patch.object(ve, "_get_pro")
    def test_etf_pool_filters_tech_excludes_cross_border(self, pro):
        import pandas as pd
        df = pd.DataFrame([
            {"ts_code": "512760.SH", "name": "半导体ETF", "fund_type": "股票型"},
            {"ts_code": "513100.SH", "name": "纳指ETF", "fund_type": "股票型"},
            {"ts_code": "518880.SH", "name": "黄金ETF", "fund_type": "股票型"},
            {"ts_code": "510300.SH", "name": "沪深300ETF", "fund_type": "股票型"},
            {"ts_code": "161226.SZ", "name": "白银LOF", "fund_type": "股票型"},
        ])
        pro.return_value.fund_basic.return_value = df
        pool = ve._etf_pool()
        self.assertEqual(pool, ["512760.SH"])  # 只留科技且非跨境

    def test_etf_candidates_matches_signal(self):
        import pandas as pd
        bars = _etf_bars()
        dates = pd.date_range("2026-01-01", periods=len(bars), freq="D")
        df = pd.DataFrame([{
            "ts_code": "512760.SH", "trade_date": str(dates[i])[:10],
            "open": b["open"], "high": b["high"], "low": b["low"],
            "close": b["close"], "vol": b["vol"], "total_mv": 0.0, "is_st": False,
        } for i, b in enumerate(bars)])
        cands = ve._etf_candidates(df)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["symbol"], "SH512760")

    def test_etf_candidates_rejects_overshoot(self):
        import pandas as pd
        bars = _etf_bars()
        for b in bars[-4:]:
            b["close"] += 0.12
        dates = pd.date_range("2026-01-01", periods=len(bars), freq="D")
        df = pd.DataFrame([{
            "ts_code": "512760.SH", "trade_date": str(dates[i])[:10],
            "open": b["open"], "high": b["high"], "low": b["low"],
            "close": b["close"], "vol": b["vol"], "total_mv": 0.0, "is_st": False,
        } for i, b in enumerate(bars)])
        cands = ve._etf_candidates(df)
        self.assertEqual(len(cands), 0)


class TestVrebEtfBuildSizing(unittest.TestCase):

    def test_vreb_etf_sizing_uses_short_line_tier(self):
        from app.services import t_build
        with mock.patch.object(t_build, "_params", return_value=dict(t_build.BUILD_PARAMS_DEFAULT)), \
             mock.patch.object(t_build, "t_net_asset", return_value=250000.0), \
             mock.patch.object(t_build, "_positions_value", return_value=(0.0, {})), \
             mock.patch.object(t_build, "get_sellable_ledger", return_value={}):
            s = t_build.build_sizing("SH512760", 1.2, mode="vreb_etf")
            self.assertAlmostEqual(s["single_max_amount"], 75000.0, delta=1)

    def test_vreb_etf_build_skips_timing(self):
        from app.services import t_build
        calls = {}

        def fake_gw(symbol, price, volume, reason="", decision_source="agent",
                    event_id=None, force_human=False, build_mode="standard"):
            calls["build_mode"] = build_mode
            return {"status": "success", "price": price, "volume": volume}

        with mock.patch.object(t_build, "build_gateway_execute", side_effect=fake_gw), \
             mock.patch.object(t_build, "confirm_build_timing",
                               side_effect=AssertionError("vreb_etf 不应走回踩时机确认")):
            out = t_build.build_t_position("SH512760", 1.2, volume=100,
                                           reason="vreb_etf 测试", decision_source="ai_led",
                                           build_mode="vreb_etf")
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(calls.get("build_mode"), "vreb_etf")


class TestVrebEtfExits(unittest.TestCase):
    """科技ETF出场：+6% 清 / -4% 硬止损 / 8交易日超时。"""

    def _run(self, pnl_pct, built="2026-08-01"):
        with mock.patch.object(ve, "_vreb_etf_positions",
                               return_value=[{"symbol": "SH512760", "volume": 1000,
                                              "avg_price": 1.2, "built_at": built}]), \
             mock.patch.object(ve, "fetch_realtime", return_value={"price": 1.2 * (1 + pnl_pct)}), \
             mock.patch("app.services.t_gateway.gateway_execute", return_value={"status": "success"}), \
             mock.patch.object(ve, "_trading_days_since", return_value=9 if built == "2026-08-10" else 2):
            return ve.check_exits()

    def test_stop_loss_sells(self):
        res = self._run(-0.05)
        self.assertEqual(len(res), 1)
        self.assertIn("止损", res[0]["reason"])

    def test_tp6_sells(self):
        res = self._run(0.065)
        self.assertEqual(len(res), 1)
        self.assertIn("+6%", res[0]["reason"])

    def test_below_tp_no_action(self):
        res = self._run(0.03)  # +3%：未达 +6%，不动作
        self.assertEqual(len(res), 0)

    def test_timeout_sells(self):
        res = self._run(0.01, built="2026-08-10")
        self.assertEqual(len(res), 1)
        self.assertIn("超时", res[0]["reason"])


class TestVrebEtfRealtimeDegrade(unittest.TestCase):

    def test_outside_window_skips(self):
        with mock.patch.object(ve, "_auto_build_window", return_value=False), \
             mock.patch.object(ve, "_pending_candidates",
                               return_value=[{"id": 1, "symbol": "SH512760", "score": 1.0}]), \
             mock.patch("app.services.t_build.build_t_position") as bld:
            res = ve.try_build_candidates()
        self.assertEqual(res, [])
        bld.assert_not_called()


if __name__ == "__main__":
    unittest.main()

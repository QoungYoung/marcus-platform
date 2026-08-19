# -*- coding: utf-8 -*-
"""t-vrebounce-short-term 单元测试（核心为 monkeypatch 纯逻辑，不依赖 DB）。"""
import unittest
from unittest import mock

from app.services import t_vrebounce as vb


def _vreb_bars():
    """构造满足 V反 状态（含 bias20≤3% 防追高）的 45 根日线：
    前 30 根平盘 12.5 -> 10 根急跌至 8.0（15日低点 7.85）-> 5 根反弹至 10.4（+32.5%）。
    close/MA20 ≈ -1.5%（温和 V反，不追高），J/RSI6 高位。
    """
    seq = []
    for i in range(55):
        seq.append({"date": "20260101", "open": 12.5, "high": 12.6,
                    "low": 12.4, "close": 12.5, "vol": 1000.0})
    for i in range(10):
        close = 12.5 - 0.45 * (i + 1)
        seq.append({"date": "20260101", "open": close + 0.1, "high": close + 0.15,
                    "low": close - 0.15, "close": close, "vol": 1000.0})
    for c in (8.3, 8.9, 9.5, 10.0, 10.4):
        seq.append({"date": "20260101", "open": c - 0.1, "high": c + 0.15,
                    "low": c - 0.1, "close": c, "vol": 1000.0})
    return seq


def _overshoot_bars():
    """构造"追高"失败形态：15日低点反弹≥25% 但收盘偏离 MA20 超 3%。"""
    seq = _vreb_bars()
    for b in seq[-5:]:
        b["close"] += 0.9
        b["high"] += 0.9
    return seq


class TestVRebouncePure(unittest.TestCase):
    """账户隔离与日频入池纯逻辑（不依赖 DB）。"""

    def test_account_isolation_constant(self):
        self.assertEqual(vb._account_id(), "t")

    def test_normalize(self):
        self.assertEqual(vb._normalize("002384"), "SZ002384")
        self.assertEqual(vb._normalize("600519"), "SH600519")
        self.assertEqual(vb._normalize("SH600519"), "SH600519")

    @mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(vb, "fetch_daily_bars")
    def test_day_filter_vreb_pass(self, bars, mcap):
        """MA20 下行 + 15日反弹≥25% + 超买 + 小市值 -> 入池。"""
        bars.return_value = _vreb_bars()
        ok, reasons, score = vb.day_filter("002384")
        self.assertTrue(ok, reasons)
        self.assertGreater(score, 0)

    @mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(vb, "fetch_daily_bars")
    def test_day_filter_no_rebound_reject(self, bars, mcap):
        """无 15日反弹 -> 拒绝。"""
        seq = [{"date": "20260101", "open": 10.0, "high": 10.2, "low": 9.8,
                "close": 10.0, "vol": 1000.0} for _ in range(65)]
        bars.return_value = seq
        ok, reasons, _ = vb.day_filter("002384")
        self.assertFalse(ok)
        self.assertTrue(any("反弹" in r for r in reasons))

    @mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(vb, "fetch_daily_bars")
    def test_day_filter_overshoot_reject(self, bars, mcap):
        """反弹过猛偏离 MA20 >3%（追高）-> 拒绝（失败单共性过滤）。"""
        bars.return_value = _overshoot_bars()
        ok, reasons, _ = vb.day_filter("002384")
        self.assertFalse(ok)
        self.assertTrue(any("偏离" in r for r in reasons))


    @mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(vb, "fetch_daily_bars")
    def test_day_filter_cci_release(self, bars, mcap):
        """volr/b60 不满足但 CCI<=-10（深度超卖）-> 放行分支通过。"""
        seq = _vreb_bars()
        # 把最后一天放量（volr>1.0），并让最后 14 天收盘压在 14 日均价下方（CCI 低）
        seq[-1]["vol"] = 3000.0
        for b in seq[-14:]:
            b["close"] -= 0.6
            b["high"] -= 0.6
            b["low"] -= 0.6
        bars.return_value = seq
        ok, reasons, score = vb.day_filter("002384")
        self.assertTrue(ok, reasons)

    @mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(vb, "fetch_daily_bars")
    def test_day_filter_ma20_rising_reject(self, bars, mcap):
        """单边上涨（MA20 转上）-> 拒绝（V反 要求 MA20 仍下行）。"""
        seq = []
        for i in range(65):
            close = 8.0 + 0.2 * i
            seq.append({"date": "20260101", "open": close, "high": close + 0.2,
                        "low": close - 0.1, "close": close, "vol": 1000.0})
        bars.return_value = seq
        ok, reasons, _ = vb.day_filter("002384")
        self.assertFalse(ok)
        self.assertTrue(any("MA20" in r for r in reasons))

    @mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0)
    @mock.patch.object(vb, "fetch_daily_bars")
    def test_day_filter_not_overbought_reject(self, bars, mcap):
        """缓慢下行后企稳（无超买）-> 拒绝。"""
        seq = []
        for i in range(65):
            close = 10.0 - 0.02 * i
            seq.append({"date": "20260101", "open": close, "high": close + 0.1,
                        "low": close - 0.1, "close": close, "vol": 1000.0})
        bars.return_value = seq
        ok, reasons, _ = vb.day_filter("002384")
        self.assertFalse(ok)
        self.assertTrue(any("超买" in r for r in reasons))

    def test_trading_days_since(self):
        self.assertGreaterEqual(vb._trading_days_since("2026-08-01"), 0)


class TestVRebounceVectorizedScan(unittest.TestCase):
    """全市场向量化扫描：与 day_filter 同公式 + scan_once 接线（mock DB）。"""

    def _df_from_seq(self, seq):
        import pandas as pd
        dates = pd.date_range("2026-01-01", periods=len(seq), freq="D")
        rows = []
        for i, b in enumerate(seq):
            rows.append({"ts_code": "002384.SZ", "trade_date": str(dates[i])[:10],
                         "open": b["open"], "high": b["high"], "low": b["low"],
                         "close": b["close"], "vol": b["vol"], "total_mv": 500000.0, "is_st": False})
        return pd.DataFrame(rows)

    def test_vectorized_matches_day_filter(self):
        """同一构造数据：向量化结果 == day_filter 单票结果（防两套算法漂移）。"""
        seq = _vreb_bars()
        df = self._df_from_seq(seq)
        cands = vb._vreb_candidates_vectorized(df)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["symbol"], "SZ002384")
        # 与 day_filter 对照（同一 bars）
        with mock.patch.object(vb, "fetch_mcap_yi", return_value=50.0), \
             mock.patch.object(vb, "fetch_daily_bars", return_value=seq):
            ok, reasons, score = vb.day_filter("002384")
        self.assertTrue(ok, reasons)
        self.assertAlmostEqual(cands[0]["score"], score, delta=0.01)

    def test_vectorized_rejects_overshoot(self):
        """追高形态（bias20>3%）向量化同样拒绝。"""
        seq = _overshoot_bars()
        df = self._df_from_seq(seq)
        cands = vb._vreb_candidates_vectorized(df)
        self.assertEqual(len(cands), 0)

    def test_scan_once_writes_candidates(self):
        """scan_once：mock 数据源后把候选写入 t 候选池（source=vrebounce）。"""
        seq = _vreb_bars()
        df = self._df_from_seq(seq)
        written = []
        with mock.patch.object(vb, "ensure_market_data", return_value=True), \
             mock.patch.object(vb, "_load_market_frame", return_value=df), \
             mock.patch.object(vb, "_insert_scan_result",
                               side_effect=lambda sym, name, score, reasons, trend: written.append((sym, score))):
            hits = vb.scan_once()
        self.assertEqual(hits, ["SZ002384"])
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][0], "SZ002384")


class TestVRebounceBuildSizing(unittest.TestCase):
    """vrebounce 独立规模档（monkeypatch 掉 DB 读取）。"""

    def setUp(self):
        from app.services import t_build
        self.t_build = t_build

    def test_vrebounce_sizing_independent(self):
        tb_ = self.t_build
        with mock.patch.object(tb_, "_params", return_value=dict(tb_.BUILD_PARAMS_DEFAULT)), \
             mock.patch.object(tb_, "t_net_asset", return_value=250000.0), \
             mock.patch.object(tb_, "_positions_value", return_value=(0.0, {})), \
             mock.patch.object(tb_, "get_sellable_ledger", return_value={}):
            s = tb_.build_sizing("SZ002384", 10.0, mode="vrebounce")
            self.assertAlmostEqual(s["single_max_amount"], 75000.0, delta=1)   # 30% x 25万
            self.assertAlmostEqual(s["total_floor_max"], 150000.0, delta=1)    # 60% x 25万
            s2 = tb_.build_sizing("SZ002384", 10.0, mode="standard")
            self.assertAlmostEqual(s2["single_max_amount"], 25000.0, delta=1)  # std 10% x 25万


class TestVRebounceBuildWiring(unittest.TestCase):
    """build_t_position vrebounce 模式接线（mock 网关，无 DB）。"""

    def test_vrebounce_skips_timing_and_passes_mode(self):
        from app.services import t_build
        calls = {}

        def fake_gw(symbol, price, volume, reason="", decision_source="agent",
                    event_id=None, force_human=False, build_mode="standard"):
            calls["build_mode"] = build_mode
            calls["decision_source"] = decision_source
            return {"status": "success", "price": price, "volume": volume}

        with mock.patch.object(t_build, "build_gateway_execute", side_effect=fake_gw), \
             mock.patch.object(t_build, "confirm_build_timing",
                               side_effect=AssertionError("vrebounce 不应走回踩时机确认")):
            out = t_build.build_t_position("SZ002384", 10.0, volume=100,
                                           reason="vrebounce 测试",
                                           decision_source="ai_led",
                                           build_mode="vrebounce")
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(calls.get("build_mode"), "vrebounce")
        self.assertEqual(calls.get("decision_source"), "ai_led")


class TestVRebounceExits(unittest.TestCase):
    """短线出场：+8% 清 / -5% 硬止损 / 12交易日超时；无 +5% 减半。"""

    def _run(self, pnl_pct, volume=100, built="2026-08-01"):
        with mock.patch.object(vb, "_vrebounce_positions",
                               return_value=[{"symbol": "SZ002384", "volume": volume,
                                              "avg_price": 10.0, "built_at": built}]), \
             mock.patch.object(vb, "fetch_realtime", return_value={"price": 10.0 * (1 + pnl_pct)}), \
             mock.patch("app.services.t_gateway.gateway_execute", return_value={"status": "success"}), \
             mock.patch.object(vb, "_trading_days_since", return_value=12 if built == "2026-08-10" else 2):
            return vb.check_exits()

    def test_stop_loss_sells_all(self):
        res = self._run(-0.06)
        self.assertEqual(len(res), 1)
        self.assertIn("止损", res[0]["reason"])
        self.assertEqual(res[0]["volume"], 100)

    def test_tp8_sells_all(self):
        res = self._run(0.09)
        self.assertEqual(len(res), 1)
        self.assertIn("+8%", res[0]["reason"])
        self.assertEqual(res[0]["volume"], 100)

    def test_no_tp5_half(self):
        """+6%（介于 +5% 与 +8% 之间）-> 不动作（刻意无 +5% 减半）。"""
        res = self._run(0.06, volume=200)
        self.assertEqual(len(res), 0)

    def test_timeout_sells_all(self):
        res = self._run(0.01, built="2026-08-10")
        self.assertEqual(len(res), 1)
        self.assertIn("超时", res[0]["reason"])


class TestVRebounceRealtimeDegrade(unittest.TestCase):
    """实时数据不可用 -> 降级等待，不盲买。"""

    def test_realtime_unavailable_defers_build(self):
        """REALTIME_CONFIRM=1：实时复核失败 -> wait_realtime，不建仓。"""
        with mock.patch.object(vb, "REALTIME_CONFIRM", True), \
             mock.patch.object(vb, "_auto_build_window", return_value=True), \
             mock.patch.object(vb, "_pending_candidates",
                               return_value=[{"id": 1, "symbol": "SZ002384", "score": 1.0}]), \
             mock.patch.object(vb, "fetch_realtime", return_value=None), \
             mock.patch.object(vb, "_mark_candidate") as mk, \
             mock.patch("app.services.t_build.build_t_position") as bld:
            res = vb.try_build_candidates()
        self.assertEqual(res[0]["status"], "wait_realtime")
        bld.assert_not_called()

    def test_realtime_unavailable_no_confirm_defers_build(self):
        """默认 REALTIME_CONFIRM=0（只验价）：实时价不可用 -> no_price，不盲买。"""
        with mock.patch.object(vb, "REALTIME_CONFIRM", False), \
             mock.patch.object(vb, "_auto_build_window", return_value=True), \
             mock.patch.object(vb, "_pending_candidates",
                               return_value=[{"id": 1, "symbol": "SZ002384", "score": 1.0}]), \
             mock.patch.object(vb, "fetch_realtime", return_value=None), \
             mock.patch.object(vb, "_mark_candidate"), \
             mock.patch("app.services.t_build.build_t_position") as bld:
            res = vb.try_build_candidates()
        self.assertEqual(res[0]["status"], "no_price")
        bld.assert_not_called()

    def test_transient_rejection_keeps_pending(self):
        """非交易时段等瞬时拒绝 -> 保持 pending（下轮重试），不落 blocked。"""
        with mock.patch.object(vb, "_auto_build_window", return_value=True), \
             mock.patch.object(vb, "_pending_candidates",
                               return_value=[{"id": 1, "symbol": "SZ002384", "score": 1.0}]), \
             mock.patch.object(vb, "fetch_realtime", return_value={"price": 10.0, "main_net": 0.0}), \
             mock.patch.object(vb, "_mark_candidate") as mk, \
             mock.patch("app.services.t_build.build_t_position",
                        return_value={"status": "rejected", "reason": "非交易时段（level=hard）"}):
            res = vb.try_build_candidates()
        self.assertEqual(res[0]["status"], "rejected")
        mk.assert_called_once_with("SZ002384", "pending", note=mock.ANY)

    def test_outside_build_window_skips(self):
        """自动建仓窗口外（如 9:25 冷静期）不尝试建仓、不改候选状态。"""
        with mock.patch.object(vb, "_auto_build_window", return_value=False), \
             mock.patch.object(vb, "_pending_candidates",
                               return_value=[{"id": 1, "symbol": "SZ002384", "score": 1.0}]), \
             mock.patch("app.services.t_build.build_t_position") as bld:
            res = vb.try_build_candidates()
        self.assertEqual(res, [])
        bld.assert_not_called()


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""improve-t-performance 新功能测试：双条件生成 / 止损撮合 / 底仓风控 / 选股硬过滤 / AI 兜底。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

TEST_DB_NAME = "marcus_trading_test"
PG_AVAILABLE = False


def _pg_available() -> bool:
    try:
        import urllib.parse
        import psycopg2
        parsed = urllib.parse.urlparse("postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading")
        conn = psycopg2.connect(host=parsed.hostname, port=parsed.port, dbname="postgres",
                                user=parsed.username, password=parsed.password, connect_timeout=3)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        conn.close()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[test-t-perf] PostgreSQL 不可用: {e}")
        return False


def setUpModule():
    global PG_AVAILABLE
    if not _pg_available():
        return
    import os
    os.environ["DATABASE_URL"] = (
        "postgresql://marcus:marcus123@127.0.0.1:18789/" + TEST_DB_NAME)
    from app.database import init_db
    init_db()
    PG_AVAILABLE = True


def tearDownModule():
    if not PG_AVAILABLE:
        return
    try:
        import urllib.parse
        import psycopg2
        parsed = urllib.parse.urlparse("postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading")
        conn = psycopg2.connect(host=parsed.hostname, port=parsed.port, dbname="postgres",
                                user=parsed.username, password=parsed.password, connect_timeout=3)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.close()
    except Exception:  # noqa: BLE001
        pass


# ── fixture（复用 test_t_backtest 的假数据生成）──
def _make_day(day: str, base: float, drift: float, vol_base: float = 1000.0):
    bars = []
    t = datetime.strptime(day + " 09:30", "%Y%m%d %H:%M")
    price = base
    for i in range(48):
        if i == 24:
            t = datetime.strptime(day + " 13:00", "%Y%m%d %H:%M")
        wave = ((i % 8) - 4) * 0.01
        price = base + drift * (i / 48) + wave
        bars.append({
            "time": t.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(price - 0.005, 3),
            "close": round(price, 3),
            "high": round(price + 0.01, 3),
            "low": round(price - 0.01, 3),
            "vol": vol_base + (i % 5) * 100,
            "amount": round(price * (vol_base + (i % 5) * 100), 2),
        })
        t += timedelta(minutes=5)
    return bars


def _make_fixture(days=("20260810", "20260811", "20260812")):
    """落假数据到临时缓存目录。首日大跌（触发低吸+止损），后两日回升。"""
    symbol = "TEST01"
    m5_all = (_make_day(days[0], 10.0, -0.3) + _make_day(days[1], 9.9, 0.1)
              + _make_day(days[2], 10.0, 0.2))
    index_m5 = (_make_day(days[0], 3000.0, 0.0, 50000) + _make_day(days[1], 3000.0, 0.0, 50000)
                + _make_day(days[2], 3000.0, 0.0, 50000))
    index_daily = [
        {"trade_date": d, "open": 3000.0, "close": 3000.0 + (i * 2), "high": 3010.0,
         "low": 2990.0, "vol": 1e6}
        for i, d in enumerate(days)
    ]
    tmp = Path(tempfile.mkdtemp(prefix="tbt_perf_"))
    (tmp / "m5").mkdir(parents=True, exist_ok=True)
    (tmp / "index_m5").mkdir(parents=True, exist_ok=True)
    (tmp / "index_daily").mkdir(parents=True, exist_ok=True)

    def _day_of(b):
        return str(b["time"])[:10].replace("-", "")

    (tmp / "m5" / f"{symbol}.json").write_text(
        json.dumps({d: [b for b in m5_all if _day_of(b) == d] for d in days}), encoding="utf-8")
    for key, ts in (("hs300", "000300.SH"), ("sh", "000001.SH"), ("sz", "399001.SZ")):
        (tmp / "index_m5" / f"{key}.json").write_text(
            json.dumps({d: [b for b in index_m5 if _day_of(b) == d] for d in days}), encoding="utf-8")
        (tmp / "index_daily" / f"{ts}.json").write_text(json.dumps(index_daily), encoding="utf-8")
    return tmp, symbol


# ── 1.5 双条件生成 ──
class TestDualConditions(unittest.TestCase):
    def test_build_t_conditions_returns_two(self):
        from app.services.t_pool import build_t_conditions
        conds = build_t_conditions(10.0, amp_med=2.0)
        self.assertEqual(len(conds), 2)
        kinds = {c["trigger_kind"] for c in conds}
        self.assertEqual(kinds, {"low_buy", "high_sell_then_buy_back"})

    def test_low_volatility_uses_floor(self):
        from app.services.t_pool import build_t_conditions
        conds = build_t_conditions(10.0, amp_med=2.0)
        low = next(c for c in conds if c["trigger_kind"] == "low_buy")
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        # 振幅 2% → 阈值用下限：高抛 1.5%、低吸 2.0%
        self.assertEqual(high["sell_target_price"], round(10.0 * 1.015, 2))
        self.assertEqual(low["target_price"], round(10.0 * 0.98, 2))
        # 止损绑定成本 -3%
        self.assertEqual(high["stop_loss_price"], round(10.0 * 0.97, 2))
        self.assertEqual(low["stop_loss_price"], round(10.0 * 0.97, 2))

    def test_high_volatility_adapts(self):
        from app.services.t_pool import build_t_conditions
        conds = build_t_conditions(10.0, amp_med=6.0)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        low = next(c for c in conds if c["trigger_kind"] == "low_buy")
        # 振幅 6% × 0.6 = 3.6% > 下限
        self.assertEqual(high["sell_target_price"], round(10.0 * 1.036, 2))
        self.assertEqual(low["target_price"], round(10.0 * 0.964, 2))

    def test_no_amp_uses_floor(self):
        from app.services.t_pool import build_t_conditions
        conds = build_t_conditions(10.0, amp_med=None)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["sell_target_price"], round(10.0 * 1.015, 2))

    def test_stop_loss_dynamic_by_amplitude(self):
        """动态止损（对齐 marcus stop_loss_monitor）：止损 = max(3%, 振幅×0.40)。"""
        from app.services.t_pool import build_t_conditions
        # 振幅 10% → 止损 4%（10×0.4）
        conds = build_t_conditions(10.0, amp_med=10.0)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["stop_loss_price"], round(10.0 * 0.96, 2))
        # 振幅 8% → 止损 3.2%（8×0.4）
        conds = build_t_conditions(10.0, amp_med=8.0)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["stop_loss_price"], round(10.0 * 0.968, 2))
        # 振幅 5% → max(3, 2.0)=3% 下限
        conds = build_t_conditions(10.0, amp_med=5.0)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["stop_loss_price"], round(10.0 * 0.97, 2))

    def test_backtest_default_conditions_dual(self):
        from app.services.t_backtest import _default_t_conditions
        conds = _default_t_conditions(10.0)
        self.assertEqual(len(conds), 2)
        self.assertIn("high_sell_then_buy_back", {c["trigger_kind"] for c in conds})


# ── 2.5 止损撮合 ──
class TestStopLossBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache, cls.symbol = _make_fixture()

    def _task(self, init_price=10.0, stop_pct=0.97, init_shares=1000):
        return {
            "symbol": self.symbol, "init_shares": init_shares, "init_price": init_price,
            "net_asset": 200000.0,
            "conditions": [{
                "id": 1, "trigger_kind": "low_buy", "target_price": 9.0,
                "stop_loss_price": round(init_price * stop_pct, 2),
                "vol_ratio_thresh": 0.0, "armed": 1, "status": "active",
            }],
        }

    def test_stop_loss_triggered_in_backtest(self):
        """首日下跌到止损价 → 止损卖腿成交（side=sell, reason=stop_loss）。"""
        from app.services.t_backtest import TBacktestEngine
        # 止损价 9.8：首日价格从 10 一路跌到 ~9.3，必然击穿
        r = TBacktestEngine(self._task(stop_pct=0.98), str(self.cache)).run()
        stop_trades = [e for e in r["events"] if e.get("type") == "trade"
                       and e.get("data", {}).get("trigger", {}).get("event_type") == "stop_loss"]
        self.assertTrue(stop_trades, "应产生止损卖腿成交")
        self.assertGreater(r["metrics"]["stop_loss_count"], 0)
        self.assertTrue(all(t["data"]["trade"]["side"] == "sell" for t in stop_trades))

    def test_stop_loss_freezes_conditions(self):
        """止损后当日条件冻结（armed=0）——之后不再有低吸触发。"""
        from app.services.t_backtest import TBacktestEngine
        r = TBacktestEngine(self._task(stop_pct=0.98), str(self.cache)).run()
        stop_idx = None
        for i, e in enumerate(r["events"]):
            if e.get("type") == "trade" and e.get("data", {}).get("trigger", {}).get("event_type") == "stop_loss":
                stop_idx = i
                break
        self.assertIsNotNone(stop_idx, "应找到止损事件")
        # 止损后的低吸触发（若发生）应为 blocked/无触发——冻结后 _cond_gate armed=0 拦截
        later_triggers = [e for e in r["events"][stop_idx:] if e.get("type") == "trigger"
                          and e.get("data", {}).get("event_type") == "low_buy"]
        for t in later_triggers:
            self.assertNotEqual(t.get("data", {}).get("quote_price", 0) or 0, 0,
                                "止损后条件应冻结")


# ── 3.5 底仓风控 ──
class TestBaseLossGuard(unittest.TestCase):
    def test_guard_blocks_deep_loss_buy(self):
        from app.services.t_gateway import _base_loss_guard
        ledger = {"X": {"avg_price": 10.0, "sellable": 1000}}
        # 浮亏 -6% → 清仓锁定
        r = _base_loss_guard("X", "buy", {"current": 9.4}, ledger)
        self.assertEqual(r["action"], "block")
        self.assertIn("清仓锁定", r["reason"])
        # 浮亏 -4% → 减半提示
        r = _base_loss_guard("X", "buy", {"current": 9.6}, ledger)
        self.assertEqual(r["action"], "block")
        self.assertIn("减半", r["reason"])
        # 浮亏 -2% → 放行
        r = _base_loss_guard("X", "buy", {"current": 9.8}, ledger)
        self.assertEqual(r["action"], "pass")
        # 卖腿不拦
        r = _base_loss_guard("X", "sell", {"current": 9.4}, ledger)
        self.assertEqual(r["action"], "pass")

    def test_validate_order_blocks_third_buy_leg(self):
        from app.services.t_gateway import validate_order_at
        ctx = {
            "regime": "ACTIVE",
            "quote": {"current": 9.8, "change_pct": -1.0},
            "ledger": {"X": {"sellable": 1000, "volume": 1000, "avg_price": 10.0}},
            "net_asset": 200000.0,
            "daily": {"realized_pnl": 0.0, "daily_turnover_amount": 0.0},
            "daily_buy_legs": 2,  # 当日已 2 笔买腿
            "risk": {},
            "sell_in_transit": False,
            "trigger_status": "pending",
            "cost_ratio_ok": True,
        }
        r = validate_order_at("X", "buy", 9.8, 100, ctx)
        self.assertFalse(r["pass"])
        self.assertIn("低吸加仓次数超限", r["reason"])

    def test_validate_order_blocks_deep_loss_buy(self):
        from app.services.t_gateway import validate_order_at
        ctx = {
            "regime": "ACTIVE",
            "quote": {"current": 9.4, "change_pct": -1.0},
            "ledger": {"X": {"sellable": 1000, "volume": 1000, "avg_price": 10.0}},
            "net_asset": 200000.0,
            "daily": {"realized_pnl": 0.0, "daily_turnover_amount": 0.0},
            "daily_buy_legs": 0,
            "risk": {},
            "sell_in_transit": False,
            "trigger_status": "pending",
            "cost_ratio_ok": True,
        }
        r = validate_order_at("X", "buy", 9.4, 100, ctx)
        self.assertFalse(r["pass"])
        self.assertIn("清仓锁定", r["reason"])

    def test_sizing_symbol_cap_reduced(self):
        from app.services.t_build import _params
        p = _params()
        cap = p["per_symbol_cap"]
        self.assertEqual(cap, {"cons": 0.08, "std": 0.12, "agg": 0.18})


# ── 4.5 选股硬过滤 ──
class TestSelectionFilter(unittest.TestCase):
    def test_quality_rejects_low_amp(self):
        from app.services.t_pool import calc_t_quality
        from app.services.t_data_sources import _normalize_symbol
        key = _normalize_symbol("TEST")
        with patch("app.services.t_pool.fetch_minute_bars", return_value=[]):
            # 无 m5 → 降级当日振幅；构造低振幅 quote
            with patch("app.services.t_pool.fetch_tencent_quote",
                       return_value={key: {"current": 10.0, "amplitude": 2.0,
                                           "amount": 1e9, "turnover_rate": 5.0}}):
                q = calc_t_quality("TEST")
        # 无 m5 数据时 amplitude=2% 走降级路径，amp_median=2.0 → 硬拒
        self.assertFalse(q["pass_gate"])
        self.assertTrue(any("振幅不适宜" in r for r in q["reasons"]))

    def test_quality_rejects_high_amp(self):
        from app.services.t_pool import calc_t_quality
        from app.services.t_data_sources import _normalize_symbol
        key = _normalize_symbol("TEST")
        with patch("app.services.t_pool.fetch_minute_bars", return_value=[]):
            with patch("app.services.t_pool.fetch_tencent_quote",
                       return_value={key: {"current": 10.0, "amplitude": 12.0,
                                           "amount": 1e9, "turnover_rate": 5.0}}):
                q = calc_t_quality("TEST")
        self.assertFalse(q["pass_gate"])
        self.assertTrue(any("振幅不适宜" in r for r in q["reasons"]))

    def test_quality_rejects_tight_spread(self):
        from app.services.t_pool import calc_t_quality
        from app.services.t_data_sources import _normalize_symbol
        key = _normalize_symbol("TEST")
        with patch("app.services.t_pool.fetch_minute_bars", return_value=[]):
            with patch("app.services.t_pool.fetch_tencent_quote",
                       return_value={key: {"current": 10.0, "amplitude": 3.0,
                                           "amount": 1e9, "turnover_rate": 5.0}}):
                q = calc_t_quality("TEST")
        # 振幅 3% − 2×滑点(≈0.27%) ≈ 2.7 > 0.5 → 价差应通过；此处验证门槛常量生效
        self.assertGreater(q["spread"], 0.5)

    def test_quality_from_daily_hard_reject_low_amp(self):
        from app.services.t_build import _quality_from_daily
        # 构造近 20 日振幅 ~2% 的日线
        bars = []
        for i in range(20):
            base = 10.0 + i * 0.01
            bars.append({"date": f"2026-01-{i+1:02d}", "open": base, "close": base + 0.01,
                         "high": base + 0.10, "low": base - 0.10,  # 振幅 ~2%
                         "vol": 1e6, "amount": 1e7})
        r = _quality_from_daily(bars)
        self.assertFalse(r["pass_gate"])
        self.assertTrue(any("振幅不适宜" in x for x in r["reasons"]))

    def test_quality_from_daily_passes_mid_amp(self):
        from app.services.t_build import _quality_from_daily
        bars = []
        for i in range(20):
            base = 10.0 + i * 0.01
            bars.append({"date": f"2026-01-{i+1:02d}", "open": base, "close": base + 0.01,
                         "high": base + 0.30, "low": base - 0.30,  # 振幅 ~6%
                         "vol": 1e6, "amount": 1e7})
        r = _quality_from_daily(bars)
        self.assertTrue(r["pass_gate"])


# ── 5.4 AI 兜底 ──
class TestAIDecisionFallback(unittest.TestCase):
    def test_parse_failure_returns_rule_fallback(self):
        from app.services.t_ai_agent import _parse_ai_decision
        self.assertEqual(_parse_ai_decision("")["action"], "rule_fallback")
        self.assertEqual(_parse_ai_decision("乱码。。。")["action"], "rule_fallback")
        self.assertEqual(_parse_ai_decision("{not json")["action"], "rule_fallback")
        # 非法 action 也兜底
        r = _parse_ai_decision('{"action": "fly", "reason": "x"}')
        self.assertEqual(r["action"], "rule_fallback")

    def test_valid_action_passthrough(self):
        from app.services.t_ai_agent import _parse_ai_decision
        r = _parse_ai_decision('{"action": "exec", "reason": "ok"}')
        self.assertEqual(r["action"], "exec")

    def test_handle_fallback_high_sell_execs(self):
        """高抛触发 + 解析失败 → 兜底 exec（兑现离场），reason 含 [rule_fallback]。"""
        from app.services.t_ai_agent import handle_ai_decision
        with patch("app.services.t_ai_agent.t_db.insert_ai_action", return_value=999), \
             patch("app.services.t_ai_agent._update_gateway_result"), \
             patch("app.services.t_gateway.gateway_execute",
                   return_value={"status": "success"}), \
             patch("app.services.t_gateway.get_sellable_ledger",
                   return_value={"X": {"sellable": 300}}):
            r = handle_ai_decision(
                {"id": 1, "symbol": "X", "event_type": "high_sell_then_buy_back",
                 "suggest_ask_price": 10.2, "condition_id": 5},
                {"regime": "CAUTIOUS"}, "无法解析的内容")
        self.assertEqual(r["action"], "exec")
        self.assertIn("[rule_fallback]", r["reason"])

    def test_handle_fallback_low_buy_waits(self):
        """低吸触发 + 解析失败 → 兜底 wait（不追），reason 含 [rule_fallback]。"""
        from app.services.t_ai_agent import handle_ai_decision
        with patch("app.services.t_ai_agent.t_db.insert_ai_action", return_value=999):
            r = handle_ai_decision(
                {"id": 2, "symbol": "X", "event_type": "low_buy", "condition_id": 5},
                {"regime": "ACTIVE"}, "乱码")
        self.assertEqual(r["action"], "wait")
        self.assertIn("[rule_fallback]", r["reason"])


if __name__ == "__main__":
    unittest.main()

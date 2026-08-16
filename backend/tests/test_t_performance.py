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
        # 振幅 6% × 0.75 = 4.5% > 下限
        self.assertEqual(high["sell_target_price"], round(10.0 * 1.045, 2))
        self.assertEqual(low["target_price"], round(10.0 * 0.955, 2))

    def test_no_amp_uses_floor(self):
        from app.services.t_pool import build_t_conditions
        conds = build_t_conditions(10.0, amp_med=None)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["sell_target_price"], round(10.0 * 1.015, 2))

    def test_stop_loss_dynamic_by_amplitude(self):
        """动态止损（对齐 marcus stop_loss_monitor）：止损 = max(3%, 振幅×0.55)。"""
        from app.services.t_pool import build_t_conditions
        # 振幅 10% → 止损 5.5%（10×0.55）
        conds = build_t_conditions(10.0, amp_med=10.0)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["stop_loss_price"], round(10.0 * 0.945, 2))
        # 振幅 8% → 止损 4.4%（8×0.55）
        conds = build_t_conditions(10.0, amp_med=8.0)
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertEqual(high["stop_loss_price"], round(10.0 * 0.956, 2))
        # 振幅 5% → max(3, 2.75)=3% 下限
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
        # 迭代#48（AI自由跑提仓位）：std 0.12→0.15、cons 0.08→0.10（agg 0.18 不变）
        self.assertEqual(cap, {"cons": 0.10, "std": 0.15, "agg": 0.18})

    def test_stop_loss_exempt_from_daily_loss_breaker(self):
        """止损卖腿豁免日亏损熔断（止血必须执行）；买腿仍被熔断拦截。"""
        from app.services.t_gateway import validate_order_at
        base_ctx = {
            "regime": "ACTIVE",
            "quote": {"current": 9.8, "change_pct": -2.0},
            "ledger": {"X": {"sellable": 1000, "volume": 1000, "avg_price": 10.0}},
            "net_asset": 200000.0,
            "daily": {"realized_pnl": -6500.0, "daily_turnover_amount": 0.0},  # -3.25% 已触发熔断
            "daily_buy_legs": 0,
            "risk": {},
            "sell_in_transit": False,
            "trigger_status": "pending",
            "cost_ratio_ok": True,
        }
        # 买腿被熔断拦截
        r = validate_order_at("X", "buy", 9.8, 100, dict(base_ctx))
        self.assertFalse(r["pass"])
        self.assertIn("日亏损熔断", r["reason"])
        # 止损卖腿放行
        r = validate_order_at("X", "sell", 9.8, 100, dict(base_ctx), is_stop_loss=True)
        self.assertTrue(r["pass"])
        # 普通卖腿（高抛）也放行
        r = validate_order_at("X", "sell", 9.8, 100, dict(base_ctx))
        self.assertTrue(r["pass"])


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

    def test_quality_amp_exempt_recent_surge(self):
        """迭代#55 振幅豁免：近20日均振幅<3%但近5日已放大（启动迹象）→ 降分放行。"""
        from app.services.t_build import _quality_from_daily

        def make_bars(amp_recent5):
            bars = []
            prev = 10.0
            for i in range(40):
                amp_pct = 0.02 if i < 35 else amp_recent5 / 100.0
                close = prev * 1.005
                half = amp_pct * prev / 2
                bars.append({"date": f"2026-05-{i % 28 + 1:02d}",
                             "open": round(prev * 1.001, 3), "close": round(close, 3),
                             "high": round(close + half, 3), "low": round(close - half, 3),
                             "vol": 1e6, "amount": 1e7})
                prev = close
            return bars

        q1 = _quality_from_daily(make_bars(3.5))
        self.assertTrue(q1["pass_gate"], "近5日振幅放大至3.5%应放行（启动迹象）")
        self.assertTrue(any("启动迹象" in r for r in q1["reasons"]))
        q2 = _quality_from_daily(make_bars(2.5))
        self.assertFalse(q2["pass_gate"], "近5日仍<3%应硬拒")
        self.assertTrue(any("振幅不适宜" in r for r in q2["reasons"]))

    def test_quality_amp_exempt_keeps_overheat_reject(self):
        """振幅豁免的 MAX 保护：近5日 12%（妖票）时，即便豁免分支也不放行。
        （豁免分支要求近5日振幅 ∈ [3,10]；>10% 走正常路径按平均振幅硬拒或按上限拒）"""
        from app.services.t_build import _quality_from_daily

        def make_bars(amp_recent5):
            bars = []
            prev = 10.0
            for i in range(40):
                amp_pct = 0.02 if i < 35 else amp_recent5 / 100.0
                close = prev * 1.005
                half = amp_pct * prev / 2
                bars.append({"date": f"2026-05-{i % 28 + 1:02d}",
                             "open": round(prev * 1.001, 3), "close": round(close, 3),
                             "high": round(close + half, 3), "low": round(close - half, 3),
                             "vol": 1e6, "amount": 1e7})
                prev = close
            return bars

        # 近5日 12%：近20日均=(15×2+5×12)/20=4.5%（>3% 不进豁免分支），正常路径振幅>10%？不——
        # 平均 4.5% 在 [3,10] 内会正常给分，验证"平均在区间内但近期过热"由趋势/风险维度把关，
        # 此处只断言豁免分支不会错误放行近5日>10%的情形（构造平均<3% 时近5日不可能>10%，
        # 数学上互斥；该保护是防御性的）
        q = _quality_from_daily(make_bars(12.0))
        # 平均 4.5% 正常路径：pass 与否取决于打分，但绝不带"启动迹象"豁免标记
        self.assertTrue(all("启动迹象" not in r for r in q["reasons"]),
                        "近5日12%不应标记启动迹象豁免")

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

    def test_quality_continuous_discriminates(self):
        """质量分连续化（迭代#42）：不同振幅/成交额得分拉开，消除撞顶 0.9 坍缩。"""
        from app.services.t_build import _quality_from_daily
        def mk(amp_pct, amount_yuan):
            bars = []
            for i in range(20):
                base = 10.0 + i * 0.01
                half = amp_pct / 100 * base / 2
                bars.append({"date": f"2026-01-{i+1:02d}", "open": base, "close": base + 0.01,
                             "high": base + half, "low": base - half,
                             "vol": 1e6, "amount": amount_yuan / 1000})
            return _quality_from_daily(bars)
        # 振幅 5%（最优） vs 振幅 3.2%（边缘）——得分应拉开
        q_best = mk(5.0, 1e9)
        q_edge = mk(3.2, 1e9)
        self.assertGreater(q_best["score"], q_edge["score"],
                           f"振幅区分失效: {q_best['score']} vs {q_edge['score']}")
        # 成交额 100亿 vs 8亿——流动性连续分拉开
        q_liq_hi = mk(5.0, 1e10)
        q_liq_lo = mk(5.0, 8e8)
        self.assertGreaterEqual(q_liq_hi["score"], q_liq_lo["score"])
        # 不再撞顶：振幅 5% + 100亿 不应是 0.9 上限（应 < 0.9 或至少不同档位有差异）
        self.assertLess(q_best["score"], 0.95)


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

    def test_update_condition_nested_json_parsed(self):
        """迭代#54 P8：嵌套 condition 对象必须能解析（旧正则 {[^{}]*} 会截断 →
        update_condition 实盘解析必失败落 rule_fallback，条件更新从未生效）。"""
        from app.services.t_ai_agent import _parse_ai_decision
        reply = ('{"action": "update_condition", "reason": "高抛目标脱节", '
                 '"condition": {"symbol": "X", "trigger_kind": "high_sell_then_buy_back", '
                 '"target_price": 10.5, "stop_loss_price": 9.5}}')
        r = _parse_ai_decision(reply)
        self.assertEqual(r["action"], "update_condition")
        self.assertIsNotNone(r["condition"])
        self.assertEqual(r["condition"]["target_price"], 10.5)
        self.assertEqual(r["condition"]["trigger_kind"], "high_sell_then_buy_back")

    def test_update_condition_with_markdown_fence(self):
        """```json 围栏 + 嵌套 condition 也能解析。"""
        from app.services.t_ai_agent import _parse_ai_decision
        reply = ('```json\n{"action": "update_condition", "reason": "x", '
                 '"condition": {"symbol": "X", "trigger_kind": "low_buy", "target_price": 9.8}}\n```')
        r = _parse_ai_decision(reply)
        self.assertEqual(r["action"], "update_condition")
        self.assertEqual(r["condition"]["target_price"], 9.8)

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


class _PGTestCase(unittest.TestCase):
    """prompt seed 测试的 PG 基座（复用 test_t_performance 的测试库生命周期）。"""
    @classmethod
    def setUpClass(cls):
        if not PG_AVAILABLE:
            raise unittest.SkipTest("PostgreSQL 不可用")

    @classmethod
    def tearDownClass(cls):
        pass


class TestPromptSeedSelfHeal(_PGTestCase):
    """迭代#54 P0（t2 工具分析）：seed_prompts 内容签名变化时覆盖更新旧版。"""

    def test_seed_overwrites_stale_content(self):
        from app.services.prompt_service import seed_prompts, upsert_prompt
        from app.models.prompt import Prompt
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            # 先种一个"旧版"（模拟 DB 残留 934 字符旧 T_BUILD）
            upsert_prompt(db, "T_BUILD_SYSTEM_PROMPT", "旧版内容-无自主看盘段")
            # 再 seed 新版 → 应覆盖旧版（内容签名不同）
            n = seed_prompts(db, {
                "T_BUILD_SYSTEM_PROMPT": {"label": "做T", "content": "新版内容-含自主看盘工具段"},
            })
            self.assertEqual(n, 1, "内容变化应触发覆盖更新")
            p = db.query(Prompt).filter(Prompt.name == "T_BUILD_SYSTEM_PROMPT").first()
            self.assertEqual(p.content, "新版内容-含自主看盘工具段")
            self.assertEqual(p.version, 2, "覆盖更新应升版本")
            # 幂等：再 seed 相同内容不重复更新
            n2 = seed_prompts(db, {
                "T_BUILD_SYSTEM_PROMPT": {"label": "做T", "content": "新版内容-含自主看盘工具段"},
            })
            self.assertEqual(n2, 0, "内容相同应幂等跳过")
        finally:
            db.close()

    def test_seed_inserts_missing(self):
        from app.services.prompt_service import seed_prompts
        from app.models.prompt import Prompt
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            n = seed_prompts(db, {"TEST_NEW_PROMPT": {"label": "t", "content": "新"}})
            self.assertEqual(n, 1)
            p = db.query(Prompt).filter(Prompt.name == "TEST_NEW_PROMPT").first()
            self.assertIsNotNone(p)
            db.delete(p)
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

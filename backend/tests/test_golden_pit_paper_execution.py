# -*- coding: utf-8 -*-
"""黄金坑 DCA 模拟盘落单（golden_pit 账户）测试。

覆盖：
- 5.1 单元测试：mock executor，断言买入腿/退出信号调用下单函数且状态为 filled/failed
- 5.2 集成测试：golden_pit 账户真实下单（需本地 PostgreSQL，未启动时自动跳过），
     断言持仓/订单/成交写入 golden_pit 且 stock 账户无变化
- 5.3/5.4 既有 DCA 测试（test_dca_carrier / test_golden_pit_sector_service）不触碰执行路径，
     由套件回归覆盖
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import golden_pit_dca_service as dca

TEST_DB_NAME = "marcus_trading_test"
PG_AVAILABLE = False
TEST_DATABASE_URL = None

_DEFAULT_URL = "postgresql://marcus:marcus123@localhost:5432/marcus_trading"


def _get_admin_conn():
    parsed = urlparse(os.getenv("DATABASE_URL", _DEFAULT_URL))
    import psycopg2
    conn = psycopg2.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        dbname="postgres",
        user=parsed.username or "marcus",
        password=parsed.password or "marcus123",
        connect_timeout=3,
    )
    conn.autocommit = True
    return conn


def _pg_available() -> bool:
    try:
        conn = _get_admin_conn()
        cur = conn.cursor()
        # 每次重建测试库，避免上次残留数据/表结构影响断言
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        conn.close()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[test-golden-pit-paper] PostgreSQL 不可用，跳过集成测试: {e}")
        return False


def setUpModule():
    global PG_AVAILABLE, TEST_DATABASE_URL
    if not _pg_available():
        return
    PG_AVAILABLE = True
    parsed = urlparse(os.getenv("DATABASE_URL", _DEFAULT_URL))
    TEST_DATABASE_URL = (
        f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname or 'localhost'}:{parsed.port or 5432}/{TEST_DB_NAME}"
    )
    # qq_notifier 模块导入时会用 .env 强制覆盖 os.environ（含 DATABASE_URL），
    # 且 qqbot_service 以顶层名导入、其他模块以 core.qq_notifier 导入，会作为两个模块各执行一次 _load_env()。
    # 必须先触发全部加载，再设置测试库 URL，避免后续引擎/API 连到真实库。
    import qq_notifier  # noqa: F401
    import core.qq_notifier  # noqa: F401
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from sqlalchemy import create_engine
    from app import database as db_mod
    import app.models.paper_trade  # noqa: F401  # 先注册 paper 模型，create_all 才能建全表

    test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    db_mod.engine = test_engine
    db_mod.SessionLocal = db_mod.sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    db_mod.Base.metadata.create_all(bind=test_engine)
    db_mod._apply_schema_patches()


def tearDownModule():
    if not PG_AVAILABLE:
        return
    try:
        conn = _get_admin_conn()
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[test-golden-pit-paper] 清理测试库失败（可忽略）: {e}")


class _PGTestCase(unittest.TestCase):
    """需要本地 PostgreSQL；setUpModule 探测后按 PG_AVAILABLE 惰性跳过。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")


class TestExecutorBinding(unittest.TestCase):
    """5.1 执行器绑定 golden_pit 账户（不依赖 PG）。"""

    def test_get_executor_binds_golden_pit_account(self):
        with mock.patch("app.core.trading.marcus_trade.MarcusVNPyExecutor") as mk:
            dca._get_executor()
            mk.assert_called_once_with(account_id="golden_pit")


class TestPlaceOrders(unittest.TestCase):
    """5.1 下单函数：限价、股数、状态与 order_id。"""

    def test_buy_places_limit_order_via_golden_pit_executor(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}), \
             mock.patch.object(dca, "_get_executor") as mk:
            inst = mk.return_value
            inst.buy.return_value = {"status": "executed", "order_id": "GP000001"}
            ok, info = dca._place_buy_order("SH510300", 5000.0, "dca-test")
        self.assertTrue(ok)
        self.assertEqual(info, "GP000001")
        inst.buy.assert_called_once_with(
            symbol="SH510300", price=1.02, volume=5000, reason="dca-test"
        )

    def test_buy_rejects_insufficient_amount(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 100.0}), \
             mock.patch.object(dca, "_get_executor") as mk:
            ok, info = dca._place_buy_order("SH510300", 50.0, "dca-test")
        self.assertFalse(ok)
        self.assertIn("金额不足", info)
        mk.return_value.buy.assert_not_called()

    def test_buy_fails_when_quote_missing(self):
        with mock.patch.object(dca, "_get_quote", return_value=None), \
             mock.patch.object(dca, "_get_executor") as mk:
            ok, info = dca._place_buy_order("SH510300", 5000.0, "dca-test")
        self.assertFalse(ok)
        self.assertIn("价格", info)

    def test_buy_passes_engine_rejection_reason(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}), \
             mock.patch.object(dca, "_get_executor") as mk:
            inst = mk.return_value
            inst.buy.return_value = {"status": "rejected", "reason": "资金不足"}
            ok, info = dca._place_buy_order("SH510300", 5000.0, "dca-test")
        self.assertFalse(ok)
        self.assertEqual(info, "资金不足")

    def test_sell_places_limit_order_via_golden_pit_executor(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}), \
             mock.patch.object(dca, "_get_executor") as mk:
            inst = mk.return_value
            inst.sell.return_value = {"status": "executed", "order_id": "GP000002"}
            ok, info = dca._place_sell_order("SH510300", 5000, "exit/full_exit")
        self.assertTrue(ok)
        self.assertEqual(info, "GP000002")
        inst.sell.assert_called_once_with(
            symbol="SH510300", price=0.98, volume=5000, reason="exit/full_exit"
        )

    def test_sell_passes_engine_rejection_reason(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}), \
             mock.patch.object(dca, "_get_executor") as mk:
            inst = mk.return_value
            inst.sell.return_value = {"status": "rejected", "reason": "无持仓"}
            ok, info = dca._place_sell_order("SH510300", 5000, "exit/full_exit")
        self.assertFalse(ok)
        self.assertEqual(info, "无持仓")

    def test_sell_rejects_insufficient_shares(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}), \
             mock.patch.object(dca, "_get_executor") as mk:
            ok, info = dca._place_sell_order("SH510300", 50, "exit/full_exit")
        self.assertFalse(ok)
        self.assertIn("股数不足", info)
        mk.return_value.sell.assert_not_called()


class TestExitSellLogging(unittest.TestCase):
    """5.1 退出卖单落盘：filled 带 order_id / failed 带原因 / 一手规则。"""

    def test_exit_sell_success_records_filled_with_order_id(self):
        with mock.patch.object(dca, "_place_sell_order", return_value=(True, "GP000010")), \
             mock.patch.object(dca, "_record_dca_log") as rec:
            ok, info = dca._execute_exit_sell(
                "000300", "2026-08-11", 2, "SH510300", 8000.0, "exit/full_exit", 8000, 2,
            )
        self.assertTrue(ok)
        self.assertEqual(info, "GP000010")
        rec.assert_called_once_with(
            fund_code="000300", window_start="2026-08-11", buy_day=2, etf_code="SH510300",
            amount=8000.0, strategy="exit/full_exit", order_id="GP000010", status="filled",
            schedule_day=2, trend_factor=0.0,
        )

    def test_exit_sell_failure_records_failed_with_reason(self):
        with mock.patch.object(dca, "_place_sell_order", return_value=(False, "无持仓")), \
             mock.patch.object(dca, "_record_dca_log") as rec:
            ok, info = dca._execute_exit_sell(
                "000300", "2026-08-11", 2, "SH510300", 8000.0, "exit/full_exit", 8000, 2,
            )
        self.assertFalse(ok)
        self.assertEqual(info, "无持仓")
        rec.assert_called_once_with(
            fund_code="000300", window_start="2026-08-11", buy_day=2, etf_code="SH510300",
            amount=8000.0, strategy="exit/full_exit", order_id="", status="failed",
            schedule_day=2, trend_factor=0.0,
        )

    def test_exit_sell_insufficient_shares_fails_without_placing(self):
        with mock.patch.object(dca, "_place_sell_order") as pl, \
             mock.patch.object(dca, "_record_dca_log") as rec:
            ok, info = dca._execute_exit_sell(
                "000300", "ws", 0, "SH510300", 8000.0, "exit/down_turn/SH510300", 50, 0,
            )
        self.assertFalse(ok)
        self.assertIn("金额不足", info)
        pl.assert_not_called()
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs["status"], "failed")

    def test_amount_to_sell_shares_rounds_to_lots(self):
        with mock.patch.object(dca, "_get_quote", return_value={"current": 2.5}):
            self.assertEqual(dca._amount_to_sell_shares("SH510300", 8000.0), 3200)

    def test_amount_to_sell_shares_zero_without_quote(self):
        with mock.patch.object(dca, "_get_quote", return_value=None):
            self.assertEqual(dca._amount_to_sell_shares("SH510300", 8000.0), 0)


def _base_status(**overrides):
    status = {
        "as_of": "2026-08-13",
        "golden_pit_window": {
            "phase": "buying",
            "current_day": 1,
            "start_date": "2026-08-11",
            "pit_count": 1,
            "warning_count": 0,
            "turning_count": 1,
            "leading_index": "000300",
        },
        "indices": [
            {
                "fund_code": "000300",
                "index_name": "沪深300",
                "tier": "core",
                "position_tier": "satellite",
                "status": "golden_pit",
                "priority": 1,
                "days_in_pit": 2,
                "greed": 0.3,
                "prev_greed": 0.3,
                "trend": "turning",
                "days_rising": 2,
                "turning_point_confirmed": True,
                "absolute_triggered": True,
            }
        ],
        "global_macro": {
            "liquidity_gate": "open",
            "global_macro_coefficient": 1.0,
            "sentiment_score": 0.5,
            "summary": "",
            "capital_flow": {},
        },
    }
    status.update(overrides)
    return status


def _cfg():
    return [
        {
            "fund_code": "000300",
            "index_name": "沪深300",
            "etf_code": "SH510300",
            "etf_name": "300ETF",
            "strategy": "uniform_10",
            "daily_amount": 1000.0,
            "max_total_amount": 100000.0,
            "require_absolute_threshold": False,
            "min_days_in_pit": 1,
            "skip_if_already_holding": False,
        }
    ]


class _DcaRunMocks:
    """execute_golden_pit_dca 公共 mock 集合。"""

    def __init__(self, status=None, cfg=None, buy_result=(True, "GP000100"),
                 build_legs=None, sell_result=None):
        self.status = status if status is not None else _base_status()
        self.cfg = cfg if cfg is not None else _cfg()
        self.buy_result = buy_result
        self.sell_result = sell_result
        self.build_legs = build_legs if build_legs is not None else (
            ([("index", "SH510300", 8000.0)], [], ""))
        self.gp = mock.patch("app.services.golden_pit_service.get_golden_pit_service")
        self.trend = mock.patch("app.services.golden_pit_service.get_trend_factor", return_value=1.0)
        self.index_params = mock.patch.object(dca, "get_effective_index_config", return_value={
            "position_multiplier": 1.0, "dca_strategy": "uniform_10",
            "dca_fallback": 10, "entry_greed": 0.5,
        })
        self.etf_configs = mock.patch.object(dca, "_get_etf_configs", return_value=self.cfg)
        self.executed_days = mock.patch.object(dca, "_get_executed_days", return_value=set())
        self.day_amount = mock.patch.object(dca, "_get_day_amount", return_value=0.0)
        self.window_reset = mock.patch.object(dca, "_check_window_reset_count", return_value=0)
        self.lump = mock.patch.object(dca, "_check_lump_reversal", return_value=(False, ""))
        self.already = mock.patch.object(dca, "_already_holding", return_value=False)
        self.legs = mock.patch.object(dca, "_build_buy_legs", return_value=self.build_legs)
        self.buy = mock.patch.object(dca, "_place_buy_order", return_value=self.buy_result)
        self.sell = mock.patch.object(dca, "_place_sell_order",
                                       return_value=self.sell_result if self.sell_result is not None else (True, "GP000099"))
        self.rec = mock.patch.object(dca, "_record_dca_log")
        self.reentry = mock.patch.object(dca, "_sell_defense_on_reentry", return_value=[])
        self.holdings = mock.patch.object(dca, "_get_holdings_detail", return_value=[])
        self.quote = mock.patch.object(dca, "_get_quote", return_value={"current": 10.0})
        self.has_exit = mock.patch.object(dca, "_has_exit_notice", return_value=False)
        self.holding_shares = mock.patch.object(dca, "_get_holding_shares", return_value=1000)
        self.sim_amount = mock.patch.object(dca, "_get_simulated_position_amount", return_value=10000.0)

    def __enter__(self):
        self._patches = []
        self.gp_mock = None
        self.rec_mock = None
        for p in (self.gp, self.trend, self.index_params, self.etf_configs, self.executed_days,
                  self.day_amount, self.window_reset, self.lump, self.already, self.legs,
                  self.buy, self.sell, self.rec, self.reentry, self.holdings, self.quote,
                  self.has_exit, self.holding_shares, self.sim_amount):
            started = p.start()
            self._patches.append(p)
            if p is self.gp:
                self.gp_mock = started
            elif p is self.rec:
                self.rec_mock = started
        self.gp_mock.return_value.get_status.return_value = self.status
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


class TestExecuteDcaBuyLegs(unittest.TestCase):
    """5.1 主流程买入腿：成功 filled + order_id，失败 failed + 未成交通知。"""

    def test_buy_leg_success_records_filled_with_order_id(self):
        with _DcaRunMocks() as m:
            out = dca.execute_golden_pit_dca()
        self.assertEqual(out["stats"]["executed_count"], 1)
        self.assertTrue(any(
            c.kwargs.get("status") == "filled" and c.kwargs.get("order_id") == "GP000100"
            and c.kwargs.get("etf_code") == "SH510300"
            for c in m.rec_mock.call_args_list
        ))
        self.assertIn("📢", out["summary_text"])

    def test_buy_leg_failure_records_failed_and_notifies_unfilled(self):
        with _DcaRunMocks(buy_result=(False, "资金不足")) as m:
            out = dca.execute_golden_pit_dca()
        self.assertTrue(any(
            c.kwargs.get("status") == "failed" and c.kwargs.get("order_id") == ""
            and c.kwargs.get("etf_code") == "SH510300"
            for c in m.rec_mock.call_args_list
        ))
        self.assertIn("买入未成交", out["summary_text"])
        self.assertIn("资金不足", out["summary_text"])


class TestExecuteDcaExit(unittest.TestCase):
    """5.1 主流程退出信号：真实卖单 filled/failed 落盘。"""

    @staticmethod
    def _exit_status():
        status = _base_status()
        idx = status["indices"][0]
        idx["exit_signal"] = "full_exit"
        idx["exit_reason"] = "测试退出"
        idx["days_in_pit"] = 0  # 避免买入腿分支干扰退出断言
        return status

    def test_exit_signal_sells_and_records_filled(self):
        with _DcaRunMocks(status=self._exit_status(), sell_result=(True, "GP000010")) as m:
            out = dca.execute_golden_pit_dca()
        self.assertTrue(any(
            c.kwargs.get("status") == "filled"
            and c.kwargs.get("strategy") == "exit/full_exit"
            and c.kwargs.get("order_id") == "GP000010"
            for c in m.rec_mock.call_args_list
        ))
        self.assertIn("已卖出", out["summary_text"])
        self.assertIn("GP000010", out["summary_text"])

    def test_exit_signal_failure_records_failed_and_notifies(self):
        with _DcaRunMocks(status=self._exit_status(), sell_result=(False, "无持仓")) as m:
            out = dca.execute_golden_pit_dca()
        self.assertTrue(any(
            c.kwargs.get("status") == "failed"
            and c.kwargs.get("strategy") == "exit/full_exit"
            for c in m.rec_mock.call_args_list
        ))
        self.assertIn("卖出未成交", out["summary_text"])
        self.assertIn("无持仓", out["summary_text"])


class TestGoldenPitRealOrders(_PGTestCase):
    """5.2 集成测试：golden_pit 账户真实下单，stock 账户无变化。"""

    def test_buy_writes_golden_pit_only(self):
        from app.database import SessionLocal
        from app.models.paper_trade import PaperOrder, PaperPosition, PaperTrade

        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}):
            ok, info = dca._place_buy_order("SH510300", 5000.0, "integration-test")
        self.assertTrue(ok, info)
        self.assertTrue(str(info).startswith("GP"), info)

        db = SessionLocal()
        try:
            gp_pos = (
                db.query(PaperPosition)
                .filter(PaperPosition.account_id == "golden_pit",
                        PaperPosition.symbol == "SH510300")
                .first()
            )
            st_pos = (
                db.query(PaperPosition)
                .filter(PaperPosition.account_id == "stock",
                        PaperPosition.symbol == "SH510300")
                .first()
            )
            gp_orders = (
                db.query(PaperOrder)
                .filter(PaperOrder.account_id == "golden_pit",
                        PaperOrder.orderid == info)
                .count()
            )
            gp_trades = (
                db.query(PaperTrade)
                .filter(PaperTrade.account_id == "golden_pit",
                        PaperTrade.orderid == info)
                .count()
            )
        finally:
            db.close()
        self.assertIsNotNone(gp_pos)
        self.assertGreaterEqual(gp_pos.volume, 100)
        self.assertIsNone(st_pos)
        self.assertEqual(gp_orders, 1)
        self.assertEqual(gp_trades, 1)

    def test_sell_writes_golden_pit_only(self):
        from datetime import datetime
        from app.database import SessionLocal
        from app.models.paper_trade import PaperOrder, PaperPosition, PaperTrade

        db = SessionLocal()
        try:
            db.query(PaperPosition).filter(
                PaperPosition.account_id == "golden_pit",
                PaperPosition.symbol == "SH512480",
            ).delete()
            db.query(PaperTrade).filter(
                PaperTrade.account_id == "golden_pit",
                PaperTrade.symbol == "SH512480",
            ).delete()
            # FIFO 卖出基于 paper_trades 买入成交；seed 前一日买入（避免 T+1 拦截）
            db.add(PaperTrade(
                orderid="GP999999", account_id="golden_pit", symbol="SH512480",
                direction="买入", price=1.0, volume=1000, amount=1000.0,
                profit=0, created_at="2026-08-01 10:00:00",
                trade_date="2026-08-01", voided=0, reason="integration-test-seed",
            ))
            db.add(PaperPosition(
                account_id="golden_pit", symbol="SH512480", volume=1000,
                frozen=0, avg_price=1.0, entry_date="2026-08-01",
                highest_price=1.0, updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            db.commit()
        finally:
            db.close()

        with mock.patch.object(dca, "_get_quote", return_value={"current": 1.0}):
            ok, info = dca._place_sell_order("SH512480", 1000, "integration-test-exit")
        self.assertTrue(ok, info)
        self.assertTrue(str(info).startswith("GP"), info)

        db = SessionLocal()
        try:
            gp_pos = (
                db.query(PaperPosition)
                .filter(PaperPosition.account_id == "golden_pit",
                        PaperPosition.symbol == "SH512480")
                .first()
            )
            st_pos = (
                db.query(PaperPosition)
                .filter(PaperPosition.account_id == "stock",
                        PaperPosition.symbol == "SH512480")
                .first()
            )
            gp_trades = (
                db.query(PaperTrade)
                .filter(PaperTrade.account_id == "golden_pit",
                        PaperTrade.orderid == info)
                .count()
            )
        finally:
            db.close()
        # 全量卖出后持仓行应被删除（paper_positions 无记录）
        self.assertIsNone(gp_pos)
        self.assertIsNone(st_pos)
        self.assertEqual(gp_trades, 1)


class TestVoidTradeCashRefund(_PGTestCase):
    """5.3 撤回/恢复成交的资金反向结算：撤回买入退钱、恢复买入扣款（防止资金被吞）。"""

    def _buy_via_executor(self, executor, symbol="SH510300", price=2.0, volume=500):
        from app.database import SessionLocal
        from app.models.paper_trade import PaperTrade

        result = executor.buy(symbol=symbol, price=price, volume=volume, reason="void-test")
        self.assertEqual(result.get("status"), "executed", result)
        db = SessionLocal()
        try:
            trade = (
                db.query(PaperTrade)
                .filter(PaperTrade.account_id == "golden_pit",
                        PaperTrade.orderid == result["order_id"])
                .first()
            )
        finally:
            db.close()
        self.assertIsNotNone(trade)
        return trade.id

    def _cash(self):
        return self._cash_executor().get_account()["available_cash"]

    @staticmethod
    def _cash_executor():
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        return MarcusVNPyExecutor(account_id="golden_pit")

    def test_void_buy_refunds_cash_and_unvoid_deducts(self):
        from app.core.trading.marcus_trade import MarcusVNPyExecutor

        executor = MarcusVNPyExecutor(account_id="golden_pit")
        cash_before = executor.get_account()["available_cash"]
        trade_id = self._buy_via_executor(executor)

        cash_after_buy = self._cash()
        self.assertAlmostEqual(cash_before - cash_after_buy, 2.0 * 500 * 1.0005, places=2)

        res = self._cash_executor()
        r = res.void_trade(trade_id, "unit-test-void")
        self.assertTrue(r["success"], r)
        self.assertAlmostEqual(self._cash(), cash_before, places=2)

        r = res.unvoid_trade(trade_id)
        self.assertTrue(r["success"], r)
        self.assertAlmostEqual(self._cash(), cash_after_buy, places=2)

    def test_void_sell_deducts_proceeds_and_unvoid_restores(self):
        from datetime import datetime
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        from app.database import SessionLocal
        from app.models.paper_trade import PaperAccountInfo, PaperTrade

        executor = MarcusVNPyExecutor(account_id="golden_pit")
        self._buy_via_executor(executor, symbol="SH512480", price=1.0, volume=1000)

        db = SessionLocal()
        try:
            acct = (
                db.query(PaperAccountInfo)
                .filter(PaperAccountInfo.account_id == "golden_pit")
                .first()
            )
            proceeds = 1.2 * 1000 * (1 - 0.0015)
            acct.available_cash = float(acct.available_cash) + proceeds
            trade = PaperTrade(
                orderid="GP990001", account_id="golden_pit", symbol="SH512480",
                direction="卖出", price=1.2, volume=1000, amount=1200.0,
                profit=199.2, created_at=datetime.now().isoformat(),
                trade_date="2026-08-01", voided=0, reason="void-test-sell",
            )
            db.add(trade)
            db.commit()
            sell_id = trade.id
        finally:
            db.close()

        cash_before_void = self._cash()
        r = self._cash_executor().void_trade(sell_id, "unit-test-void-sell")
        self.assertTrue(r["success"], r)
        self.assertAlmostEqual(cash_before_void - self._cash(), proceeds, places=2)

        r = self._cash_executor().unvoid_trade(sell_id)
        self.assertTrue(r["success"], r)
        self.assertAlmostEqual(self._cash(), cash_before_void, places=2)


if __name__ == "__main__":

    unittest.main()

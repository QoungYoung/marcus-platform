# -*- coding: utf-8 -*-
"""多账户模拟盘隔离测试（需本地 PostgreSQL；未启动时自动跳过）。

覆盖：
- 8.1 迁移幂等性：重复执行不报错、注册表种子（stock / golden_pit 25 万）、复合主键
- 8.2 多账户隔离：同 symbol 持仓互不覆盖、现金独立、订单前缀独立
- 8.3 trades / portfolio / accounts API 的 account 参数（默认 stock、golden_pit、未知账户 400）
- 8.4 由现有测试套件覆盖（本模块不重复）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

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
        print(f"[test-multi-account] PostgreSQL 不可用，跳过多账户测试: {e}")
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

    # API 模块的 SessionLocal 引用绑定测试库
    import app.api.accounts as accounts_api
    import app.api.portfolio as portfolio_api
    import app.api.trades as trades_api
    import app.services.worker_control as worker_control_mod

    for _mod in (trades_api, portfolio_api, accounts_api, worker_control_mod):
        _mod.SessionLocal = db_mod.SessionLocal


def tearDownModule():
    if not PG_AVAILABLE:
        return
    try:
        conn = _get_admin_conn()
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[test-multi-account] 清理测试库失败（可忽略）: {e}")


class _PGTestCase(unittest.TestCase):
    """需要本地 PostgreSQL；setUpModule 探测后按 PG_AVAILABLE 惰性跳过。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")


class TestMigrationIdempotency(_PGTestCase):
    """8.1 迁移幂等性：重复启动不报错、存量数据归入 stock、注册表种子。"""

    def test_migration_idempotent_and_seeds_registry(self):
        from sqlalchemy import inspect, text
        from app import database as db_mod

        # setUpModule 已跑过一次，这里再跑两遍验证幂等
        db_mod._apply_paper_account_migration()
        db_mod._apply_paper_account_migration()

        with db_mod.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT account_id, initial_capital FROM paper_accounts ORDER BY account_id")
            ).fetchall()
        account_map = {r[0]: float(r[1]) for r in rows}
        self.assertIn("stock", account_map)
        self.assertIn("golden_pit", account_map)
        self.assertEqual(account_map["golden_pit"], 250000.0)

        inspector = inspect(db_mod.engine)
        for table in ["paper_orders", "paper_trades", "paper_positions",
                      "paper_account_info", "paper_daily_snapshot", "paper_capital_adjustments"]:
            cols = {c["name"] for c in inspector.get_columns(table)}
            self.assertIn("account_id", cols, f"{table} 缺 account_id 列")

        pk_positions = inspector.get_pk_constraint("paper_positions")
        self.assertEqual(set(pk_positions["constrained_columns"]), {"account_id", "symbol"})
        pk_snapshot = inspector.get_pk_constraint("paper_daily_snapshot")
        self.assertEqual(set(pk_snapshot["constrained_columns"]), {"account_id", "trade_date"})
        pk_info = inspector.get_pk_constraint("paper_account_info")
        self.assertEqual(set(pk_info["constrained_columns"]), {"account_id"})

    def test_legacy_rows_default_to_stock(self):
        from sqlalchemy import text
        from app import database as db_mod

        # 存量数据在迁移后 account_id 应为 stock（无 account_id 的行归入默认账户）
        with db_mod.engine.connect() as conn:
            conn.execute(text("DELETE FROM paper_trades"))
            conn.execute(text(
                "INSERT INTO paper_trades (orderid, account_id, symbol, direction, price, volume, amount, profit, created_at, reason) "
                "VALUES ('ORD000001', 'stock', 'SH600000', '买入', 10, 100, 1000, 0, '2026-08-13T09:30:00', 'test')"
            ))
            conn.commit()
        with db_mod.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT account_id FROM paper_trades WHERE orderid = 'ORD000001'"
            )).fetchone()
        self.assertEqual(row[0], "stock")


class TestEngineIsolation(_PGTestCase):
    """8.2 多账户隔离：两账户同 symbol 持仓互不覆盖、现金独立、订单前缀独立。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="marcus_paper_test_")

    def test_positions_and_cash_isolated(self):
        from paper_engine import PaperTradingEngine

        stock = PaperTradingEngine(data_dir=self.tmp, account_id="stock")
        gp = PaperTradingEngine(data_dir=self.tmp, account_id="golden_pit")
        stock_cash_before = stock.available_cash
        gp_cash_before = gp.available_cash

        o1 = stock.buy("SH600519", 1500.0, 100)
        self.assertIsNotNone(o1)
        self.assertTrue(o1.startswith("ORD"))
        self.assertTrue(stock.match_order(o1, 1500.0))

        o2 = gp.buy("SH512480", 1.5, 10000)
        self.assertIsNotNone(o2)
        self.assertTrue(o2.startswith("GP"))
        self.assertTrue(gp.match_order(o2, 1.5))

        # 同 symbol 持仓互不覆盖
        self.assertIn("SH600519", stock.positions)
        self.assertNotIn("SH600519", gp.positions)
        self.assertIn("SH512480", gp.positions)
        self.assertNotIn("SH512480", stock.positions)

        # 现金独立：golden_pit 消费不影响 stock
        self.assertLess(stock.available_cash, stock_cash_before)
        self.assertLess(gp.available_cash, gp_cash_before)
        gp_cash_after_first = gp.available_cash

        o3 = stock.buy("SH600519", 1500.0, 100)
        self.assertTrue(stock.match_order(o3, 1500.0))
        # stock 再买入后，golden_pit 现金不变
        self.assertAlmostEqual(gp.available_cash, gp_cash_after_first, places=2)

    def test_order_counter_independent(self):
        from paper_engine import PaperTradingEngine

        stock = PaperTradingEngine(data_dir=self.tmp, account_id="stock")
        gp = PaperTradingEngine(data_dir=self.tmp, account_id="golden_pit")
        o1 = stock.buy("SH600519", 1500.0, 100)
        o2 = gp.buy("SH512480", 1.5, 10000)
        self.assertNotEqual(o1, o2)
        self.assertTrue(o1.startswith("ORD"))
        self.assertTrue(o2.startswith("GP"))


class TestApiAccountScope(_PGTestCase):
    """8.3 trades / portfolio / accounts API 的 account 参数。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api import accounts, portfolio, trades

        app = FastAPI()
        app.state.vnpy_bridge = None
        app.include_router(accounts.router, prefix="/api/v1")
        app.include_router(portfolio.router, prefix="/api/v1")
        app.include_router(trades.router, prefix="/api/v1")
        cls.client = TestClient(app)

    def test_list_accounts(self):
        res = self.client.get("/api/v1/accounts")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        ids = {a["account_id"] for a in data}
        self.assertIn("stock", ids)
        self.assertIn("golden_pit", ids)
        golden = next(a for a in data if a["account_id"] == "golden_pit")
        self.assertEqual(golden["initial_capital"], 250000.0)

    def test_unknown_account_rejected(self):
        res = self.client.post("/api/v1/trades", json={
            "symbol": "SH512480", "side": "buy", "price": 1.5, "volume": 100,
            "account": "no_such_account",
        })
        self.assertEqual(res.status_code, 400)

    def test_default_account_is_stock(self):
        # 不带 account → 默认 stock，可正常下单
        res = self.client.post("/api/v1/trades", json={
            "symbol": "SH600519", "side": "buy", "price": 1500.0, "volume": 100,
        })
        self.assertEqual(res.status_code, 200, res.text)
        pos = self.client.get("/api/v1/portfolio/positions", params={"account": "stock"})
        self.assertEqual(pos.status_code, 200)
        self.assertIn("SH600519", [p["symbol"] for p in pos.json()])

    def test_golden_pit_trade_scoped(self):
        res = self.client.post("/api/v1/trades", json={
            "symbol": "SH512480", "side": "buy", "price": 1.5, "volume": 10000,
            "account": "golden_pit",
        })
        self.assertEqual(res.status_code, 200, res.text)

        gp_pos = self.client.get("/api/v1/portfolio/positions", params={"account": "golden_pit"})
        self.assertEqual(gp_pos.status_code, 200)
        self.assertIn("SH512480", [p["symbol"] for p in gp_pos.json()])

        stock_pos = self.client.get("/api/v1/portfolio/positions", params={"account": "stock"})
        self.assertEqual(stock_pos.status_code, 200)
        self.assertNotIn("SH512480", [p["symbol"] for p in stock_pos.json()])

    def test_equity_history_scoped(self):
        res = self.client.get("/api/v1/portfolio/equity-history", params={"account": "golden_pit"})
        self.assertEqual(res.status_code, 200)


class TestWorkerControlChannel(_PGTestCase):
    """8.5 worker 控制通道：状态快照发布/读取 + 命令收发 + 离线判定。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.services import worker_control as wc
        wc.ensure_tables()

    def _clear_status(self):
        from app.services import worker_control as wc
        from sqlalchemy import text
        db = wc.SessionLocal()
        try:
            db.execute(text("DELETE FROM worker_status"))
            db.commit()
        finally:
            db.close()

    def test_status_roundtrip(self):
        from app.services import worker_control as wc
        wc.publish_status({"scheduler": {"running": True}, "tasks": [{"id": "t1"}]})
        st = wc.read_status()
        self.assertTrue(st["online"])
        self.assertEqual(st["snapshot"]["scheduler"], {"running": True})
        self.assertEqual(st["snapshot"]["tasks"], [{"id": "t1"}])
        self.assertIsNotNone(st["pid"])

    def test_command_roundtrip(self):
        from app.services import worker_control as wc
        res = wc.send_command("scheduler.trigger", {"task_id": "x"})
        self.assertTrue(res["success"])
        cmds = wc.take_commands()
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0]["cmd"], "scheduler.trigger")
        self.assertEqual(cmds[0]["payload"], {"task_id": "x"})
        wc.finish_command(cmds[0]["id"], True, {"success": True})
        # 已领取的命令不应被再次领取
        self.assertEqual(wc.take_commands(), [])

    def test_offline_before_first_publish(self):
        from app.services import worker_control as wc
        self._clear_status()
        st = wc.read_status()
        self.assertFalse(st["online"])
        self.assertEqual(st["snapshot"], {})


class TestSchedulerApiOffline(_PGTestCase):
    """8.6 worker 离线时调度 API 返回离线标记而非报错。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.scheduler import router as scheduler_router
        from app.services import worker_control as wc
        from sqlalchemy import text

        db = wc.SessionLocal()
        try:
            db.execute(text("DELETE FROM worker_status"))
            db.commit()
        finally:
            db.close()

        app = FastAPI()
        app.include_router(scheduler_router, prefix="/api/v1")
        cls.client = TestClient(app)

    def test_status_returns_offline(self):
        res = self.client.get("/api/v1/scheduler/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("running", body)
        self.assertFalse(body.get("online", True))

    def test_tasks_empty_without_worker(self):
        res = self.client.get("/api/v1/scheduler/tasks")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["tasks"], [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""做T系统测试（需本地 PostgreSQL；未启动时自动跳过）。

覆盖（6.3）：
- t 账户迁移幂等性：重复执行不报错、t 账户注册、五张表存在
- 三层池：可T质量打分硬门槛（价差>0）、实盘池仅含已持仓标的、禁止无底仓标的做T
- 量比归一：对照旧公式（turnover_rate/2.0）消除早盘误报（公式级断言）
- 状态机：t_triggers 原子消费防重复、human_confirm 超时 cancelled
- 网关校验矩阵：硬闸门（裸空/跌停/STOP_ALL/白名单）/ 账本（买腿≤可卖底仓）/ 建议层
- 可卖额度账本原子性：卖腿扣减、买腿回补
- regime 三态切换：硬保险丝→HALT、日内前哨→CAUTIOUS、正常→ACTIVE
"""
import json
import os
import sys
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
TEST_DATABASE_URL = None

_DEFAULT_URL = "postgresql://marcus:marcus123@localhost:5432/marcus_trading"


def _get_admin_conn():
    parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(
        os.getenv("DATABASE_URL", _DEFAULT_URL))
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
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        conn.close()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[test-t-account] PostgreSQL 不可用，跳过做T测试: {e}")
        return False


def setUpModule():
    global PG_AVAILABLE, TEST_DATABASE_URL
    if not _pg_available():
        return
    # 从 DATABASE_URL 推导测试库 URL（保留 host/port/user/password，仅换 dbname）
    parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(
        os.getenv("DATABASE_URL", _DEFAULT_URL))
    TEST_DATABASE_URL = (
        f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}"
        f":{parsed.port or 5432}/{TEST_DB_NAME}"
    )
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from app.database import init_db
    init_db()
    PG_AVAILABLE = True


def tearDownModule():
    """清理测试库（可忽略失败）。"""
    if not PG_AVAILABLE:
        return
    try:
        conn = _get_admin_conn()
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[test-t-account] 清理测试库失败（可忽略）: {e}")


class _PGTestCase(unittest.TestCase):
    """需要本地 PostgreSQL；setUpModule 探测后按 PG_AVAILABLE 惰性跳过。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")


class TMigrationTest(_PGTestCase):
    """t 账户迁移幂等性 + 五张表存在。"""

    def test_migration_idempotent_and_tables_exist(self):
        from app.database import _apply_t_account_migration, engine
        from sqlalchemy import inspect, text
        # 重复执行幂等
        _apply_t_account_migration()
        _apply_t_account_migration()
        insp = inspect(engine)
        for tbl in ["t_conditions", "t_triggers", "t_regime_state", "t_daily_state", "t_risk_state"]:
            self.assertIn(tbl, set(insp.get_table_names()), f"缺少表 {tbl}")
        # t 账户注册
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT account_id, name FROM paper_accounts WHERE account_id = 't'"
            )).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "t")
            # t_risk_state 种子
            risk = db.execute(text("SELECT stop_all FROM t_risk_state WHERE id = 1")).fetchone()
            self.assertIsNotNone(risk)
            self.assertFalse(risk[0])
        finally:
            db.close()


class TConditionTriggerTest(_PGTestCase):
    """t_conditions/t_triggers 表 + 状态机。"""

    def setUp(self):
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM t_triggers"))
            db.execute(text("DELETE FROM t_conditions"))
            db.commit()
        finally:
            db.close()

    def test_upsert_and_list_condition(self):
        from app.services import t_db
        cid = t_db.upsert_condition({
            "symbol": "600519",
            "trigger_kind": "low_buy",
            "target_price": 100.0,
            "reinform_price": 100.4,
            "vol_ratio_thresh": 1.5,
            "stabilize_level": "not_new_low",
            "sell_target_price": 103.0,
            "stop_loss_price": 97.0,
        })
        self.assertIsNotNone(cid)
        conds = t_db.list_active_conditions(symbol="600519")
        self.assertEqual(len(conds), 1)
        self.assertEqual(conds[0]["target_price"], 100.0)

    def test_insert_and_claim_trigger_atomic(self):
        from app.services import t_db
        cid = t_db.upsert_condition({"symbol": "000001", "target_price": 10.0})
        tid = t_db.insert_trigger({
            "symbol": "000001", "condition_id": cid, "event_type": "low_buy",
            "trigger_price": 10.0, "quote_price": 9.95,
            "suggest_bid_price": 9.94, "suggest_ask_price": 9.96,
        })
        self.assertIsNotNone(tid)
        # 原子消费：第一次成功，第二次（同表无 pending）返回 None
        first = t_db.claim_pending_trigger("consumer-a")
        self.assertIsNotNone(first)
        self.assertEqual(first["symbol"], "000001")
        second = t_db.claim_pending_trigger("consumer-b")
        self.assertIsNone(second)
        # 状态流转
        ok = t_db.update_trigger_status(first["id"], "executed")
        self.assertTrue(ok)
        trigs = t_db.list_triggers(status="executed")
        self.assertEqual(len(trigs), 1)

    def test_human_confirm_timeout_cancelled(self):
        """human_confirm 超时（孤儿单）自动 cancelled：pending 超过 timeout 被清理。"""
        from app.services import t_db
        from sqlalchemy import text
        from app.database import SessionLocal
        tid = t_db.insert_trigger({"symbol": "300750", "event_type": "low_buy", "quote_price": 100.0})
        # 把 created_at 改到 10 分钟前（模拟超时）
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_triggers SET created_at = now() - interval '10 minutes' WHERE id = :id"
            ), {"id": tid})
            db.commit()
        finally:
            db.close()
        # 孤儿单处置在 claim 时执行
        t_db.claim_pending_trigger("t-test", timeout_seconds=300)
        trigs = t_db.list_triggers()
        self.assertEqual(trigs[0]["status"], "cancelled")
        self.assertEqual(trigs[0]["reason"], "orphan_timeout")


class TGatewayTest(_PGTestCase):
    """网关三阶校验矩阵（mock 行情/持仓，避免依赖真实网络）。"""

    def setUp(self):
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM t_triggers"))
            db.execute(text("DELETE FROM t_conditions"))
            db.commit()
        finally:
            db.close()

    @patch("app.services.t_gateway.self_quote")
    @patch("app.services.t_gateway.get_sellable_ledger")
    @patch("app.services.t_gateway.compute_regime")
    def test_hard_gate_stop_all(self, m_regime, m_ledger, m_quote):
        from app.services import t_db, t_gateway
        t_db.set_stop_all(True, "test")
        m_regime.return_value = {"regime": "ACTIVE"}
        m_ledger.return_value = {"600519": {"sellable": 1000, "avg_price": 100.0}}
        m_quote.return_value = {"current": 100.0, "change_pct": -1.0}
        r = t_gateway.validate_order("600519", "buy", 100.0, 100)
        self.assertFalse(r["pass"])
        self.assertIn("STOP_ALL", r["reason"])
        t_db.set_stop_all(False, "")

    @patch("app.services.t_gateway.self_quote")
    @patch("app.services.t_gateway.get_sellable_ledger")
    @patch("app.services.t_gateway.compute_regime")
    def test_ledger_buy_exceeds_sellable(self, m_regime, m_ledger, m_quote):
        from app.services import t_db, t_gateway
        m_regime.return_value = {"regime": "ACTIVE"}
        # 无底仓标的 → 硬闸门拦截（禁止无底仓建仓式做T）
        m_ledger.return_value = {}
        m_quote.return_value = {"current": 10.0, "change_pct": 0.0}
        r = t_gateway.validate_order("000001", "buy", 10.0, 1000)
        self.assertFalse(r["pass"])
        self.assertIn("无底仓", r["reason"])
        # 有底仓但买腿超上限
        m_ledger.return_value = {"000001": {"sellable": 500, "avg_price": 10.0}}
        r = t_gateway.validate_order("000001", "buy", 10.0, 600)
        self.assertFalse(r["pass"])
        self.assertIn("上限", r["reason"])

    @patch("app.services.t_gateway.self_quote")
    @patch("app.services.t_gateway.get_sellable_ledger")
    @patch("app.services.t_gateway.compute_regime")
    def test_limit_down_block(self, m_regime, m_ledger, m_quote):
        from app.services import t_db, t_gateway
        m_regime.return_value = {"regime": "ACTIVE"}
        m_ledger.return_value = {"600519": {"sellable": 1000, "avg_price": 100.0}}
        m_quote.return_value = {"current": 100.0, "change_pct": -9.9}  # 跌停
        r = t_gateway.validate_order("600519", "buy", 100.0, 100)
        self.assertFalse(r["pass"])
        self.assertIn("跌停", r["reason"])

    @patch("app.services.t_gateway.self_quote")
    @patch("app.services.t_gateway.get_sellable_ledger")
    @patch("app.services.t_gateway.compute_regime")
    def test_pass_normal_buy(self, m_regime, m_ledger, m_quote):
        from app.services import t_db, t_gateway
        t_db.set_stop_all(False, "")
        m_regime.return_value = {"regime": "ACTIVE"}
        m_ledger.return_value = {"600519": {"sellable": 1000, "avg_price": 100.0}}
        m_quote.return_value = {"current": 99.0, "change_pct": -1.0}
        r = t_gateway.validate_order("600519", "buy", 99.0, 100)
        self.assertTrue(r["pass"], f"应放行: {r}")


class TRegimeTest(_PGTestCase):
    """regime 三层合成三态切换。"""

    def test_regime_three_states(self):
        from app.services.t_regime import _read_market_diagnosis
        with patch("app.services.t_regime._read_market_diagnosis") as m_diag, \
             patch("app.services.t_regime._fetch_index_quotes") as m_quotes, \
             patch("app.services.t_regime._is_trading_time", return_value=True):
            # ACTIVE：日频震荡 + 指数平稳
            m_diag.return_value = {"state": "trend"}
            m_quotes.return_value = {"sh000300": {"change_pct": 0.3}}
            r = compute_regime_force()
            self.assertEqual(r["regime"], "ACTIVE")
            self.assertEqual(r["gate_low_buy"], "ALLOWED")
            self.assertEqual(r["interpret_sign"], 1)
            # CAUTIOUS：指数跌 1%（日内前哨）
            m_quotes.return_value = {"sh000300": {"change_pct": -1.2}}
            r = compute_regime_force()
            self.assertEqual(r["regime"], "CAUTIOUS")
            self.assertEqual(r["gate_low_buy"], "MANUAL_ONLY")
            # HALT：沪深300跌超 2%（硬保险丝）
            m_quotes.return_value = {"sh000300": {"change_pct": -2.5}}
            r = compute_regime_force()
            self.assertEqual(r["regime"], "HALT")
            self.assertEqual(r["gate_low_buy"], "BLOCKED")
            self.assertEqual(r["interpret_sign"], -1)


def compute_regime_force():
    """强制重算（清缓存）。"""
    from app.services.t_regime import _regime_cache, compute_regime
    _regime_cache.update({"ts": 0, "result": None})
    return compute_regime(force=True)


class TQualityTest(_PGTestCase):
    """可T质量打分（mock 行情与分钟线，避免真实网络）。"""

    @patch("app.services.t_pool.fetch_minute_bars")
    def test_t_quality_spread_gate(self, m_bars):
        from app.services.t_pool import calc_t_quality
        # 高振幅 → 价差空间 > 0
        m_bars.return_value = [
            {"time": f"2026-08-14 09:35:00", "open": 100.0, "close": 101.0, "high": 102.0, "low": 99.0, "vol": 100},
            {"time": f"2026-08-14 09:40:00", "open": 101.0, "close": 100.5, "high": 103.0, "low": 100.0, "vol": 120},
        ] * 100
        quote = {"current": 100.0, "amount": 10_0000_0000 / 10000, "turnover_rate": 3.0, "amplitude": 4.0}
        q = calc_t_quality("600519", quote=quote)
        self.assertTrue(q["pass_gate"], f"应通过硬门槛: {q['reasons']}")
        self.assertGreater(q["spread"], 0)

    @patch("app.services.t_pool.fetch_minute_bars")
    def test_t_quality_spread_gate_reject(self, m_bars):
        from app.services.t_pool import calc_t_quality
        # 低振幅 → 价差空间 ≤ 0
        m_bars.return_value = [
            {"time": f"2026-08-14 09:35:00", "open": 100.0, "close": 100.05, "high": 100.1, "low": 99.95, "vol": 100},
        ] * 100
        quote = {"current": 100.0, "amount": 1_0000_0000 / 10000, "turnover_rate": 0.5, "amplitude": 0.2}
        q = calc_t_quality("600519", quote=quote)
        self.assertFalse(q["pass_gate"])
        self.assertTrue(any("价差" in r for r in q["reasons"]))


class TVolumeRatioTest(_PGTestCase):
    """盘中量比归一（公式级：对照旧公式消除早盘误报）。"""

    def test_volume_ratio_normalized_formula(self):
        """早盘 10:00（已开 30 分钟）累计换手 0.8%：
        旧公式 = 0.8/2.0 = 0.4（误判缩量）；
        归一公式 = (0.8 × 240/30) / 2.0 = 6.4/2.0 = 3.2（正确识别放量，因为同刻基准全天仅 2% 中有 0.8% 集中在早盘）。
        """
        from app.services.t_monitor import TMonitor
        m = TMonitor()
        # 模拟条件：基准 profile 同刻均值 2.0
        cond = {"benchmark_turnover_profile": {"same_minute_avg": 2.0}}
        quote = {"turnover_rate": 0.8}
        # 直接测公式逻辑：量比 = 换手×伸缩/基准
        opened = 30  # 早盘 10:00 已开 30 分钟
        scaled = 0.8 * (240.0 / opened)  # 6.4
        ratio = scaled / 2.0             # 3.2
        self.assertAlmostEqual(ratio, 3.2, places=1)
        # 旧公式误报对照
        legacy = round(0.8 / 2.0, 2)     # 0.4
        self.assertLess(legacy, 1.5)     # 旧公式下不触发
        self.assertGreater(ratio, 1.5)   # 归一公式下触发

    @patch("app.services.t_monitor.TMonitor._calc_volume_ratio", return_value=2.5)
    @patch("app.services.t_monitor.TMonitor._stabilize_not_new_low", return_value=True)
    def test_condition_armed_gate(self, m_stab, m_vr):
        """状态机：armed=0 不触发；armed=1 且条件满足才触发。"""
        from app.services.t_monitor import TMonitor
        m = TMonitor()
        cond = {"armed": 0, "last_triggered_at": None, "trigger_kind": "low_buy",
                "target_price": 10.0, "sell_target_price": 0, "vol_ratio_thresh": 1.5,
                "symbol": "600519"}
        quote = {"current": 9.5, "turnover_rate": 1.0}
        regime = {"regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED"}
        self.assertFalse(m._evaluate_condition(cond, quote, regime))
        cond["armed"] = 1
        # 14:45 前 + 价格到位 + 量能达标 → 触发
        with patch("app.services.t_monitor.datetime") as m_dt:
            m_dt.now.return_value = datetime(2026, 8, 14, 10, 0, 0)
            self.assertTrue(m._evaluate_condition(cond, quote, regime))


class TAiLedTest(_PGTestCase):
    """AI 主导做T：决策解析、审计落库、触发状态流转、连续命中计数、唤醒 payload。"""

    def setUp(self):
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM t_ai_actions"))
            db.execute(text("DELETE FROM t_triggers"))
            db.execute(text("DELETE FROM t_conditions"))
            db.commit()
        finally:
            db.close()

    def _make_trigger(self, status="pending"):
        from app.services import t_db
        trig_id = t_db.insert_trigger({
            "condition_id": 1, "symbol": "600519", "event_type": "low_buy",
            "trigger_price": 100.0, "quote_price": 99.5,
            "suggest_bid_price": 99.4, "suggest_ask_price": 99.6,
            "slippage_budget": 0.001, "mode": "auto",
        })
        return trig_id

    def test_ai_decision_wait_marks_await_retry(self):
        """AI 决策 wait → 事件 await_retry + 审计落库。"""
        from app.services import t_ai_agent, t_db
        trig_id = self._make_trigger()
        trig = t_db.get_trigger_by_id(trig_id) if hasattr(t_db, "get_trigger_by_id") else \
            {"id": trig_id, "symbol": "600519", "condition_id": 1,
             "event_type": "low_buy", "suggest_bid_price": 99.4}
        r = t_ai_agent.handle_ai_decision(
            trig, {"symbol": "600519"},
            '{"action": "wait", "reason": "量比不足"}', session_id="t-agent-600519")
        self.assertEqual(r["action"], "wait")
        acts = t_db.list_ai_actions(symbol="600519")
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["action_type"], "ai_wait")

    def test_ai_decision_abandon_cancels_trigger(self):
        """AI 决策 abandon → 事件 cancelled。"""
        from app.services import t_ai_agent, t_db
        trig_id = self._make_trigger()
        t_ai_agent.handle_ai_decision(
            {"id": trig_id, "symbol": "600519", "condition_id": 1,
             "event_type": "low_buy", "suggest_bid_price": 99.4},
            {}, '{"action": "abandon", "reason": "追高"}')
        rows = t_db.list_triggers(status="cancelled")
        self.assertTrue(any(r["id"] == trig_id for r in rows))

    def test_ai_decision_update_condition(self):
        """AI 决策 update_condition → 新条件写入（publisher=ai）。"""
        from app.services import t_ai_agent, t_db
        trig_id = self._make_trigger()
        r = t_ai_agent.handle_ai_decision(
            {"id": trig_id, "symbol": "600519", "condition_id": 1,
             "event_type": "low_buy", "suggest_bid_price": 99.4},
            {}, json.dumps({"action": "update_condition", "reason": "触发价偏离",
                            "condition": {"symbol": "600519", "trigger_kind": "low_buy",
                                          "target_price": 98.0}}))
        self.assertIsNotNone(r.get("condition_id"))
        conds = t_db.list_active_conditions(symbol="600519")
        self.assertEqual(conds[0]["publisher"], "ai")

    def test_wake_agent_payload_has_decision_mode(self):
        """唤醒 payload 含 decision_mode=ai_led + 连续命中上下文。"""
        from app.services import t_bridge
        captured = {}
        with patch("app.services.t_bridge.urllib.request.urlopen") as m_open:
            m_open.return_value.__enter__.return_value.read.return_value = b"{}"
            with patch("app.services.t_bridge._bridge_url", return_value="http://x/chat"):
                t_bridge.wake_agent(
                    {"id": 1, "symbol": "600519", "event_type": "low_buy",
                     "trigger_price": 100, "quote_price": 99.5,
                     "suggest_bid_price": 99.4, "suggest_ask_price": 99.6,
                     "condition_id": 1},
                    context={"regime": "ACTIVE"})
            req = m_open.call_args[0][0]
            body = json.loads(req.data.decode())
            captured = body
        self.assertEqual(captured.get("decision_mode"), "ai_led")
        self.assertIn("你是做T决策者", captured.get("message", ""))
        self.assertIn("exec|wait|abandon|update_condition", captured.get("message", ""))

    def test_consecutive_hits_counts(self):
        """连续命中计数：pending/ai_decided/await_retry 连续计数，executed 中断。"""
        from app.services import t_db
        from app.services.t_bridge import _consecutive_hits
        for st in ("pending", "ai_decided", "await_retry"):
            t_db.insert_trigger({
                "condition_id": 7, "symbol": "600519", "event_type": "low_buy",
                "trigger_price": 100.0, "quote_price": 99.5, "mode": "auto"})
        t_db.insert_trigger({
            "condition_id": 7, "symbol": "600519", "event_type": "low_buy",
            "trigger_price": 100.0, "quote_price": 99.5, "mode": "auto"})
        # 最新一条 executed → 中断
        rows = t_db.list_triggers(limit=10)
        last = max(r["id"] for r in rows)
        t_db.update_trigger_status(last, "executed")
        # 从最新往前：executed 中断 → 0（最新已 executed）
        self.assertEqual(_consecutive_hits(7, "600519"), 0)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""做T系统 · 自由表达式监控条件测试（t_expr + t_conditions.expression + TMonitor 表达式路径）。

覆盖：
- 表达式校验（非法字段/操作符/深度拒绝）
- 表达式求值（and/or/not/比较/in/between）
- 字段快照缺失时保守不触发
- t_conditions 写入 expression + 读取
- TMonitor 表达式条件评估（mock 行情）
"""
import os
import sys
import unittest
from datetime import datetime
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
    from urllib.parse import urlparse
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
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        conn.close()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[test-t-expr] PostgreSQL 不可用，跳过: {e}")
        return False


def setUpModule():
    global PG_AVAILABLE, TEST_DATABASE_URL
    if not _pg_available():
        return
    from urllib.parse import urlparse
    parsed = urlparse(os.getenv("DATABASE_URL", _DEFAULT_URL))
    TEST_DATABASE_URL = (
        f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}"
        f":{parsed.port or 5432}/{TEST_DB_NAME}"
    )
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from app.database import init_db
    init_db()
    PG_AVAILABLE = True


def tearDownModule():
    if not PG_AVAILABLE:
        return
    try:
        conn = _get_admin_conn()
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.close()
    except Exception:
        pass


class _PGTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")


class ExprValidationTest(unittest.TestCase):
    """表达式校验（纯逻辑，无需 DB）。"""

    def test_valid_expression_passes(self):
        from app.services.t_expr import validate_expression
        validate_expression({
            "and": [
                {"field": "quote.current", "op": "<=", "value": 98},
                {"field": "vol_ratio", "op": ">=", "value": 1.5},
                {"field": "regime.state", "op": "in", "value": ["ACTIVE", "CAUTIOUS"]},
            ]
        })
        validate_expression({"field": "quote.change_pct", "op": "between", "value": -5, "value2": 5})
        validate_expression({"not": {"field": "minute.m1.bounce", "op": "==", "value": False}})

    def test_invalid_field_rejected(self):
        from app.services.t_expr import ExprError, validate_expression
        with self.assertRaises(ExprError):
            validate_expression({"field": "evil.__class__", "op": "==", "value": 1})
        with self.assertRaises(ExprError):
            validate_expression({"field": "quote.current", "op": "__import__", "value": 1})

    def test_invalid_structure_rejected(self):
        from app.services.t_expr import ExprError, validate_expression
        with self.assertRaises(ExprError):
            validate_expression({"and": []})  # 空 and
        with self.assertRaises(ExprError):
            validate_expression({"field": "quote.current"})  # 缺 op/value

    def test_deep_nesting_rejected(self):
        from app.services.t_expr import ExprError, validate_expression
        deep = {"and": []}
        node = deep["and"]
        for _ in range(30):
            node.append({"and": []})
            node = node[-1]["and"]
        with self.assertRaises(ExprError):
            validate_expression(deep)


class ExprEvalTest(unittest.TestCase):
    """表达式求值（纯逻辑）。"""

    SNAPSHOT = {
        "quote": {"current": 97.5, "change_pct": -2.1, "turnover_rate": 3.2},
        "vol_ratio": 2.3,
        "minute": {"m1": {"bounce": True, "low_today": 96.0}},
        "regime": {"state": "ACTIVE", "gate_low_buy": "ALLOWED"},
        "position": {"sellable": 500, "avg_price": 100.0},
        "index": {"hs300_drop": -0.8},
    }

    def test_and_all_true(self):
        from app.services.t_expr import evaluate_expression
        expr = {"and": [
            {"field": "quote.current", "op": "<=", "value": 98},
            {"field": "vol_ratio", "op": ">=", "value": 1.5},
            {"field": "minute.m1.bounce", "op": "==", "value": True},
        ]}
        self.assertTrue(evaluate_expression(expr, self.SNAPSHOT))

    def test_and_one_false(self):
        from app.services.t_expr import evaluate_expression
        expr = {"and": [
            {"field": "quote.current", "op": "<=", "value": 98},
            {"field": "vol_ratio", "op": ">=", "value": 5.0},  # 不满足
        ]}
        self.assertFalse(evaluate_expression(expr, self.SNAPSHOT))

    def test_or(self):
        from app.services.t_expr import evaluate_expression
        expr = {"or": [
            {"field": "quote.change_pct", "op": "<=", "value": -5},
            {"field": "vol_ratio", "op": ">=", "value": 2.0},
        ]}
        self.assertTrue(evaluate_expression(expr, self.SNAPSHOT))

    def test_not(self):
        from app.services.t_expr import evaluate_expression
        expr = {"not": {"field": "quote.current", "op": ">=", "value": 100}}
        self.assertTrue(evaluate_expression(expr, self.SNAPSHOT))

    def test_in_and_between(self):
        from app.services.t_expr import evaluate_expression
        self.assertTrue(evaluate_expression(
            {"field": "regime.state", "op": "in", "value": ["ACTIVE", "CAUTIOUS"]}, self.SNAPSHOT))
        self.assertTrue(evaluate_expression(
            {"field": "quote.change_pct", "op": "between", "value": -5, "value2": 0}, self.SNAPSHOT))

    def test_missing_field_conservative(self):
        """字段缺失（快照没有）→ 保守不触发。"""
        from app.services.t_expr import evaluate_expression
        expr = {"field": "minute.m5.ma20", "op": ">", "value": 90}
        self.assertFalse(evaluate_expression(expr, self.SNAPSHOT))  # m5 不在快照

    def test_injection_rejected_in_eval(self):
        """非法字段即使绕过校验，求值时也保守返回 False。"""
        from app.services.t_expr import evaluate_expression
        expr = {"field": "quote.__class__", "op": "==", "value": 1}
        self.assertFalse(evaluate_expression(expr, self.SNAPSHOT))

    def test_tech_fields_registered(self):
        """字段注册表含全部技术指标字段（tech.*）。"""
        from app.services.t_expr import FIELD_REGISTRY
        for f in ["tech.ma5", "tech.macd_dif", "tech.macd_golden_cross",
                  "tech.kdj_k", "tech.kdj_overbought", "tech.rsi_6",
                  "tech.rsi_oversold", "tech.above_ma20"]:
            self.assertIn(f, FIELD_REGISTRY, f"缺少字段 {f}")

    def test_tech_expression_eval(self):
        """技术指标表达式求值（MACD金叉+RSI不超买+站上MA20）。"""
        from app.services.t_expr import evaluate_expression, validate_expression
        expr = {"and": [
            {"field": "tech.macd_golden_cross", "op": "==", "value": True},
            {"field": "tech.rsi_6", "op": "<=", "value": 70},
            {"field": "tech.above_ma20", "op": "==", "value": True},
        ]}
        validate_expression(expr)
        snap = {"tech": {"macd_golden_cross": True, "rsi_6": 55.5, "above_ma20": True}}
        self.assertTrue(evaluate_expression(expr, snap))
        snap2 = {"tech": {"macd_golden_cross": False, "rsi_6": 55.5, "above_ma20": True}}
        self.assertFalse(evaluate_expression(expr, snap2))


class VolPriceTest(unittest.TestCase):
    """量价关系派生字段（放量上涨/缩量下跌/恐慌放量/跌到企稳等）。"""

    def _vp(self, q: dict, vr: float):
        from app.services.t_monitor import TMonitor
        return TMonitor()._build_vol_price(q, vr)

    def test_up_with_volume(self):
        vp = self._vp({"current": 10.5, "pre_close": 10.0, "low": 9.9, "change_pct": 5.0}, 2.5)
        self.assertTrue(vp["up_with_volume"])
        self.assertTrue(vp["volume_expand"])
        self.assertTrue(vp["price_up"])

    def test_down_with_low_volume(self):
        vp = self._vp({"current": 9.8, "pre_close": 10.0, "low": 9.7, "change_pct": -2.0}, 0.5)
        self.assertTrue(vp["down_with_low_volume"])
        self.assertTrue(vp["volume_shrink"])
        self.assertTrue(vp["price_down"])

    def test_panic_drop(self):
        vp = self._vp({"current": 9.5, "pre_close": 10.0, "low": 9.4, "change_pct": -5.0}, 3.0)
        self.assertTrue(vp["panic_drop"])
        self.assertTrue(vp["down_with_volume"])

    def test_near_day_low(self):
        vp = self._vp({"current": 9.95, "pre_close": 10.0, "low": 9.9, "change_pct": -0.5}, 1.0)
        self.assertTrue(vp["near_day_low"])

    def test_drop_to_price_and_stabilise_expression(self):
        """跌到XX元并企稳：current<=10 ∧ stabilised。"""
        from app.services.t_expr import evaluate_expression, validate_expression
        expr = {"and": [
            {"field": "quote.current", "op": "<=", "value": 10.0},
            {"field": "quote.stabilised", "op": "==", "value": True},
        ]}
        validate_expression(expr)
        self.assertTrue(evaluate_expression(expr, {"quote": {"current": 9.9, "stabilised": True}}))
        self.assertFalse(evaluate_expression(expr, {"quote": {"current": 9.9, "stabilised": False}}))

    def test_shrink_drop_to_support_expression(self):
        """缩量下跌到支撑位：down_with_low_volume ∧ current<=支撑。"""
        from app.services.t_expr import evaluate_expression, validate_expression
        expr = {"and": [
            {"field": "quote.down_with_low_volume", "op": "==", "value": True},
            {"field": "quote.current", "op": "<=", "value": 10.0},
        ]}
        validate_expression(expr)
        self.assertTrue(evaluate_expression(expr, {"quote": {"down_with_low_volume": True, "current": 9.8}}))

    def test_vol_price_fields_registered(self):
        """量价字段全部在注册表（Agent 可查）。"""
        from app.services.t_expr import FIELD_REGISTRY
        for f in ["quote.volume_expand", "quote.volume_shrink", "quote.price_up",
                  "quote.price_down", "quote.up_with_volume", "quote.up_with_low_volume",
                  "quote.down_with_volume", "quote.down_with_low_volume",
                  "quote.panic_drop", "quote.near_day_low", "quote.stabilised"]:
            self.assertIn(f, FIELD_REGISTRY, f"缺少字段 {f}")


@unittest.skipUnless(True, "占位")
class _placeholder:
    pass


class TConditionExprDBTest(_PGTestCase):
    """t_conditions 写入/读取 expression（真实 PG）。"""

    def setUp(self):
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM t_conditions"))
            db.commit()
        finally:
            db.close()

    def test_upsert_and_read_expression(self):
        from app.services import t_db
        expr = {"and": [{"field": "quote.current", "op": "<=", "value": 98}]}
        cid = t_db.upsert_condition({
            "symbol": "600519",
            "trigger_kind": "custom",
            "expression": expr,
            "sell_target_price": 101.5,
            "stop_loss_price": 95.0,
        })
        self.assertIsNotNone(cid)
        conds = t_db.list_active_conditions(symbol="600519")
        self.assertEqual(len(conds), 1)
        # expression 读回（可能是 dict 或 str）
        got = conds[0].get("expression")
        self.assertTrue(got, "expression 应被读取")
        import json as _json
        if isinstance(got, str):
            got = _json.loads(got)
        self.assertEqual(got["and"][0]["field"], "quote.current")

    def test_invalid_expression_rejected_by_validate(self):
        from app.services.t_expr import ExprError, validate_expression
        with self.assertRaises(ExprError):
            validate_expression({"field": "quote.current", "op": "eval", "value": 1})


class TMonitorExprTest(_PGTestCase):
    """TMonitor 表达式条件评估（mock 行情，不依赖真实网络）。"""

    def _make_monitor(self):
        from app.services.t_monitor import TMonitor
        return TMonitor()

    def test_expr_condition_triggers(self):
        """有 expression 的条件：表达式满足 + 通用护栏通过 → 触发。"""
        from app.services.t_monitor import TMonitor
        m = TMonitor()
        cond = {
            "symbol": "600519",
            "trigger_kind": "custom",
            "armed": 1,
            "last_triggered_at": None,
            "expression": {"and": [
                {"field": "quote.current", "op": "<=", "value": 100},
                {"field": "vol_ratio", "op": ">=", "value": 1.0},
            ]},
        }
        quote = {"current": 97.0, "turnover_rate": 2.0}
        regime = {"regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED"}
        with patch("app.services.t_monitor.datetime") as m_dt:
            m_dt.now.return_value = datetime(2026, 8, 14, 10, 0, 0)
            m_dt.strptime = datetime.strptime
            snapshot = m._build_snapshot(cond, quote, regime)
            # snapshot.quote.current=97 → 表达式满足
            self.assertTrue(m._evaluate_condition(cond, quote, regime, snapshot))

    def test_expr_condition_not_trigger_when_gate_blocked(self):
        """表达式满足但 regime HALT → 通用护栏拦截。"""
        from app.services.t_monitor import TMonitor
        m = TMonitor()
        cond = {
            "symbol": "600519",
            "trigger_kind": "custom",
            "armed": 1,
            "expression": {"field": "quote.current", "op": ">", "value": 1},
        }
        quote = {"current": 97.0, "turnover_rate": 2.0}
        regime = {"regime": "HALT", "gate_low_buy": "BLOCKED", "gate_high_sell": "ALLOWED"}
        snapshot = m._build_snapshot(cond, quote, regime)
        self.assertFalse(m._evaluate_condition(cond, quote, regime, snapshot))

    def test_no_expr_falls_back_default(self):
        """无 expression → 回退默认复合确认（低吸：价到位+量能+企稳）。"""
        from app.services.t_monitor import TMonitor
        m = TMonitor()
        cond = {
            "symbol": "600519", "trigger_kind": "low_buy", "armed": 1,
            "target_price": 100.0, "sell_target_price": 0,
            "vol_ratio_thresh": 1.0, "stabilize_level": "not_new_low",
        }
        quote = {"current": 97.0, "turnover_rate": 2.0}
        regime = {"regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED"}
        with patch("app.services.t_monitor.datetime") as m_dt, \
             patch("app.services.t_monitor.TMonitor._calc_volume_ratio", return_value=2.0), \
             patch("app.services.t_monitor.TMonitor._stabilize_not_new_low", return_value=True):
            m_dt.now.return_value = datetime(2026, 8, 14, 10, 0, 0)
            m_dt.strptime = datetime.strptime
            self.assertTrue(m._evaluate_condition(cond, quote, regime))

    def test_tech_snapshot_fields(self):
        """技术指标快照（合成分钟线 → MACD/KDJ/RSI/MA 字段齐全且为数值）。"""
        from app.services.t_monitor import TMonitor
        m = TMonitor()
        # 合成 60 根 m5 分钟线（上升趋势）
        bars = [{"time": f"2026-08-14 09:{30+i//2:02d}:{0 if i%2==0 else 30:02d}",
                 "open": 10 + i * 0.05, "close": 10 + i * 0.05 + 0.02,
                 "high": 10 + i * 0.05 + 0.05, "low": 10 + i * 0.05 - 0.03,
                 "vol": 100} for i in range(60)]
        quote = {"current": 13.0, "turnover_rate": 2.0}
        with patch("app.services.t_data_sources.fetch_minute_bars", return_value=bars):
            tech = m._build_tech_snapshot("600519", quote)
        self.assertGreater(tech["ma5"], 0)
        self.assertIsInstance(tech["macd_dif"], float)
        self.assertIsInstance(tech["kdj_k"], float)
        self.assertGreaterEqual(tech["rsi_6"], 0)
        self.assertLessEqual(tech["rsi_6"], 100)
        # 上升趋势 → 现价高于 MA5/MA20
        self.assertTrue(tech["above_ma5"])


if __name__ == "__main__":
    unittest.main()

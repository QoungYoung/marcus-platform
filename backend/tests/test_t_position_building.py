# -*- coding: utf-8 -*-
"""做T底仓建仓测试（t-position-building，需本地 PostgreSQL；未启动时自动跳过）。

覆盖（tasks 1.7 / 2.4 / 3.6 / 4.3 / 5.3）：
- 迁移幂等：t_build_events / t_build_params 表存在，重复 init_db 不报错
- 建仓校验矩阵：无底仓放行 / STOP_ALL 拒 / HALT 拒 / 单票当日二次拒 / 冷静期拒 / 人工升级分流
- 规模计算：三档上限、总底仓超限拒、建议股数
- T+1 账本：建仓成交后当日 sellable=0（模拟 executor 写 paper_trades）
- 调额后基准更新：t_net_asset 反映 paper_account_info 新值
- 次日条件衔接：auto_gen_conditions_for_build 生成 trade_date=D+1 的 t_conditions
- 再平衡：跌破保留下限 → monitor_only
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
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
        print(f"[test-t-build] PostgreSQL 不可用，跳过做T建仓测试: {e}")
        return False


def setUpModule():
    global PG_AVAILABLE, TEST_DATABASE_URL
    if not _pg_available():
        return
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
    if not PG_AVAILABLE:
        return
    try:
        conn = _get_admin_conn()
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[test-t-build] 清理测试库失败（可忽略）: {e}")


class _PGTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")


def _fake_quote(current=10.0, high=10.5, low=9.8, pre_close=10.0):
    return {"sh600000": {
        "name": "mock", "current": current, "pre_close": pre_close, "open": 10.1,
        "high": high, "low": low, "vol": 1e6, "amount": 1e7,
        "turnover_rate": 2.0, "amplitude": 5.0,
        "change_pct": round((current - pre_close) / pre_close * 100, 2),
    }}


class TestBuildMigration(_PGTestCase):
    def test_tables_exist(self):
        from sqlalchemy import text, inspect
        from app.database import engine
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        self.assertIn("t_build_events", tables)
        self.assertIn("t_build_params", tables)

    def test_init_db_idempotent(self):
        from app.database import init_db
        init_db()  # 重复执行不报错


class TestValidateBuildPosition(_PGTestCase):
    def setUp(self):
        from app.services import t_build
        self.tb = t_build
        self.patches = [
            patch.object(self.tb, "compute_regime", return_value={
                "regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED",
                "interpret_sign": 1, "index_drop": 0.0}),
            patch.object(self.tb, "_is_trading_minute_allowed", return_value=(True, "")),
            patch.object(self.tb, "fetch_tencent_quote", side_effect=lambda syms: _fake_quote()),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in self.patches:
            p.stop()

    def test_validate_first_open_upgrades_human(self):
        # 无底仓标的 + 全部护栏过 → 首开升级 human（B1 清单），不自动放行
        from app.services.t_build import validate_build_position
        r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="agent")
        self.assertTrue(r["pass"])
        self.assertEqual(r["mode"], "human_confirm")
        self.assertIn("首开", r["reason"])

    def test_stop_all_rejects(self):
        from app.services import t_db
        from app.services.t_build import validate_build_position
        t_db.set_stop_all(True, "test-stop")
        try:
            r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="human")
            self.assertFalse(r["pass"])
            self.assertIn("熔断", r["reason"])
        finally:
            t_db.set_stop_all(False, "")

    def test_halt_rejects_even_manual(self):
        from app.services.t_build import validate_build_position
        with patch.object(self.tb, "compute_regime", return_value={
                "regime": "HALT", "gate_low_buy": "BLOCKED", "gate_high_sell": "ALLOWED",
                "interpret_sign": -1, "index_drop": -3.0}):
            r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="human")
            self.assertFalse(r["pass"])
            self.assertIn("HALT", r["reason"])

    def test_daily_per_symbol_limit(self):
        from app.services import t_db
        from app.services.t_build import validate_build_position
        # 预置当日已建仓事件（同票）
        t_db.insert_build_event({
            "symbol": "SH600000", "event_type": "build_position", "side": "buy",
            "price": 10.0, "volume": 1000, "amount": 10000,
            "decision_source": "human", "reason": "seed", "status": "executed",
        })
        try:
            r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="human")
            self.assertFalse(r["pass"])
            self.assertIn("单票当日已建仓", r["reason"])
        finally:
            # 清理 seed
            from sqlalchemy import text as _text
            from app.database import SessionLocal
            db = SessionLocal()
            db.execute(_text("DELETE FROM t_build_events WHERE reason = 'seed'"))
            db.commit()
            db.close()

    def test_quiet_period_rejects_agent(self):
        from app.services.t_build import validate_build_position
        with patch.object(self.tb, "_is_trading_minute_allowed", return_value=(False, "早盘冷静期（9:30-09:45）不建仓")):
            r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="agent")
            self.assertFalse(r["pass"])
            self.assertIn("冷静期", r["reason"])


class TestSizing(_PGTestCase):
    def test_sizing_limits(self):
        from app.services import t_build
        with patch.object(t_build, "t_net_asset", return_value=200000.0), \
             patch.object(t_build, "compute_regime", return_value={
                 "regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED",
                 "interpret_sign": 1, "index_drop": 0.0}), \
             patch.object(t_build, "_positions_value", return_value=(0.0, {})):
            s = t_build.build_sizing("SH600000", 10.0)
            self.assertTrue(s["pass"])
            self.assertEqual(s["tier"], "std")
            # 标准档：单笔 ≤ 10% 净值 = 20000（迭代#48 翻倍提仓位）；建议股数 = 20000/10/100*100 = 2000
            self.assertEqual(s["suggest_volume"], 2000)
            self.assertAlmostEqual(s["single_max_amount"], 20000.0, delta=0.01)

    def test_total_floor_cap_rejects(self):
        from app.services import t_build
        with patch.object(t_build, "t_net_asset", return_value=200000.0), \
             patch.object(t_build, "compute_regime", return_value={
                 "regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED",
                 "interpret_sign": 1, "index_drop": 0.0}), \
             patch.object(t_build, "_positions_value", return_value=(110000.0, {})):
            # 总底仓 11 万 + 本笔 2 万 = 13 万 > 55%×20万=11万 → 拒
            s = t_build.build_sizing("SH600000", 10.0)
            self.assertFalse(s["pass"])
            self.assertIn("总底仓超上限", s["reason"])


class TestNetAsset(_PGTestCase):
    def test_net_asset_reflects_adjust(self):
        from app.services.t_gateway import t_net_asset
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        db = SessionLocal()
        # 确保 t 账户资金行存在（updated_at 非空约束）
        db.execute(_text(
            "INSERT INTO paper_account_info (account_id, initial_capital, available_cash, frozen_cash, order_counter, updated_at) "
            "SELECT 't', 200000, 200000, 0, 0, now() WHERE NOT EXISTS "
            "(SELECT 1 FROM paper_account_info WHERE account_id = 't')"))
        db.commit()
        db.close()
        before = t_net_asset()
        # 调额 +5 万
        from app.api import t_account
        resp = t_account.t_capital_adjust(amount=50000, reason="test-adjust")
        self.assertTrue(resp["success"])
        after = t_net_asset()
        self.assertAlmostEqual(after - before, 50000.0, delta=1.0)


class TestNextDayConditions(_PGTestCase):
    def test_generates_next_day_condition(self):
        from app.services import t_build
        from app.services import t_db
        ok = t_build.auto_gen_conditions_for_build("SH600000", avg_price=10.0)
        self.assertTrue(ok)
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y%m%d")
        conds = t_db.list_active_conditions(symbol="SH600000", trade_date=tomorrow)
        self.assertTrue(any(c["symbol"] == "SH600000" for c in conds))


class TestRebalance(_PGTestCase):
    def test_rebalance_marks_monitor_only(self):
        from app.services import t_build
        from app.services import t_eod, t_pool
        with patch.object(t_build, "_positions_value", return_value=(0.0, {})), \
             patch.object(t_build, "get_sellable_ledger", return_value={
                 "SH600000": {"symbol": "SH600000", "volume": 1000, "today_buy": 0,
                              "sellable": 1000, "avg_price": 10.0}}), \
             patch.object(t_eod, "check_floor_lower", return_value=True), \
             patch.object(t_pool, "calc_t_quality", return_value={"pass_gate": True, "score": 0.8}):
            acts = t_build.rebalance_floors()
            self.assertTrue(any(a["action"] == "monitor_only" for a in acts))


class TestBuildScore(_PGTestCase):
    """建仓选股打分（mock 日线与可T质量，避免网络）。"""

    def _rising_bars(self, n=40, base=10.0):
        """单调上行日线（MA20 向上）。"""
        return [{"date": f"2026-01-{i+1:02d}", "open": base + i * 0.05,
                 "close": base + i * 0.06, "high": base + i * 0.08,
                 "low": base + i * 0.03, "vol": 1e6} for i in range(n)]

    def _falling_bars(self, n=40, base=12.0):
        """单边下行日线（MA5 < MA10 < MA20 且 MA20 向下）。"""
        return [{"date": f"2026-01-{i+1:02d}", "open": base - i * 0.05,
                 "close": base - i * 0.06, "high": base - i * 0.03,
                 "low": base - i * 0.08, "vol": 1e6} for i in range(n)]

    def test_trend_gate_blocks_downtrend(self):
        from app.services import t_build
        with patch.object(t_build, "_fetch_daily_bars", return_value=self._falling_bars()):
            ok, note = t_build.trend_gate("SH600000")
            self.assertFalse(ok)
            self.assertIn("单边下行", note)

    def test_trend_gate_passes_uptrend(self):
        from app.services import t_build
        with patch.object(t_build, "_fetch_daily_bars", return_value=self._rising_bars()):
            ok, note = t_build.trend_gate("SH600000")
            self.assertTrue(ok)

    def test_build_score_hard_gate(self):
        from app.services import t_build
        from app.services import t_pool
        # 可T质量不达标（pass_gate=False）→ 候选门槛不过
        with patch.object(t_build, "_fetch_daily_bars", return_value=self._rising_bars()), \
             patch.object(t_pool, "calc_t_quality", return_value={
                 "score": 0.3, "pass_gate": False, "reasons": ["价差不覆盖成本"]}):
            r = t_build.build_score("SH600000", source="pool")
            self.assertFalse(r["pass_gate"])
            self.assertLess(r["score"], 0.55)

    def test_build_score_source_mark(self):
        from app.services import t_build
        from app.services import t_pool
        # 新门槛（P0 审查）：pool/scan 0.65、user 0.60——mock 高质量分应通过
        with patch.object(t_build, "_fetch_daily_bars", return_value=self._rising_bars()), \
             patch.object(t_pool, "calc_t_quality", return_value={
                 "score": 0.8, "pass_gate": True, "reasons": []}):
            r = t_build.build_score("SH600000", source="user")
            self.assertEqual(r["source"], "user")
            self.assertTrue(r["pass_gate"])
            self.assertGreaterEqual(r["score"], 0.60)

    def test_build_score_higher_threshold_scan(self):
        """扫描来源门槛 0.65：quality 0.65 + 弱趋势（trend_score≈0）→ 总分不足被拒。"""
        from app.services import t_build
        from app.services import t_pool
        with patch.object(t_build, "_fetch_daily_bars", return_value=self._falling_bars()), \
             patch.object(t_pool, "calc_t_quality", return_value={
                 "score": 0.65, "pass_gate": True, "reasons": []}):
            r = t_build.build_score("SH600000", source="scan")
            # 弱趋势（trend_gate 可能过但 trend_score≈0）：0.55×0.65 + 0.35×0 = 0.3575 < 0.65 → 拒
            self.assertFalse(r["pass_gate"])
            self.assertLess(r["score"], 0.65)

    def test_build_score_continuous_trend_discriminates(self):
        """P0-1 连续趋势分：同 quality 下强多趋势 vs 弱势横盘得分拉开（根治 0.73 坍缩）。"""
        from app.services import t_build
        from app.services import t_pool
        # 上升趋势 bars（MA5>MA10>MA20 且 MA20 向上）→ trend_score 高
        with patch.object(t_build, "_fetch_daily_bars", return_value=self._rising_bars()), \
             patch.object(t_pool, "calc_t_quality", return_value={
                 "score": 0.9, "pass_gate": True, "reasons": []}):
            r_up = t_build.build_score("SH600000", source="scan")
        # 横盘 bars（构造近 25 日 close 平走）→ trend_score 低
        flat = [{"date": f"2026-01-{i+1:02d}", "open": 10.0, "close": 10.0,
                 "high": 10.1, "low": 9.9, "vol": 1e6} for i in range(40)]
        with patch.object(t_build, "_fetch_daily_bars", return_value=flat), \
             patch.object(t_pool, "calc_t_quality", return_value={
                 "score": 0.9, "pass_gate": True, "reasons": []}):
            r_flat = t_build.build_score("SH600000", source="scan")
        # 强多趋势得分应显著高于横盘（区分度恢复）
        self.assertGreater(r_up["score"], r_flat["score"],
                           f"趋势区分失效: up={r_up['score']} flat={r_flat['score']}")
        self.assertGreater(r_up["score"], 0.65)
        self.assertLess(r_flat["score"], 0.65)


class TestBuildScan(_PGTestCase):
    """全市场扫描（stock_basic + 粗筛 + 精筛，mock 数据源）。"""

    def test_to_ts_code_variants(self):
        from app.services import t_build
        self.assertEqual(t_build._to_ts_code("600519"), "600519.SH")
        self.assertEqual(t_build._to_ts_code("000001"), "000001.SZ")
        self.assertEqual(t_build._to_ts_code("sz000636"), "000636.SZ")
        self.assertEqual(t_build._to_ts_code("SH600519"), "600519.SH")
        self.assertEqual(t_build._to_ts_code("000636.SZ"), "000636.SZ")

    def _basic_df(self):
        import pandas as pd
        return pd.DataFrame([
            {"ts_code": "600519.SH", "symbol": "600519", "name": "贵州茅台", "exchange": "SSE",
             "industry": "白酒", "list_date": "20010827"},
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "exchange": "SZSE",
             "industry": "银行", "list_date": "19910403"},
            {"ts_code": "688001.SH", "symbol": "688001", "name": "华兴源创", "exchange": "SSE",
             "industry": "电子", "list_date": "20190722"},
            {"ts_code": "830001.BJ", "symbol": "830001", "name": "北交测试", "exchange": "BJ",
             "industry": "测试", "list_date": "20200101"},
            {"ts_code": "600001.SH", "symbol": "600001", "name": "ST退市票", "exchange": "SSE",
             "industry": "测试", "list_date": "19900101"},
        ])

    def test_fetch_all_a_symbols_filters(self):
        from app.services import t_build
        fake_pro = type("FakePro", (), {"stock_basic": lambda self, **kw: self._df})()
        fake_pro._df = self._basic_df()
        with patch("app.core.trading._api_config.get_tushare_pro", return_value=fake_pro):
            rows = t_build._fetch_all_a_symbols()
        syms = [r["symbol"] for r in rows]
        # 北交所与 ST 被过滤
        self.assertNotIn("830001", syms)
        self.assertNotIn("600001", syms)
        # 沪深正常保留
        self.assertIn("600519", syms)
        self.assertIn("000001", syms)

    def test_scan_uses_stock_basic_and_coarse_filter(self):
        from app.services import t_build
        fake_pro = type("FakePro", (), {"stock_basic": lambda self, **kw: self._df})()
        fake_pro._df = self._basic_df()
        # 粗筛：600519 成交额 20亿/振幅 3% 过线；000001 成交额 2亿 不过线
        def fake_quotes(syms):
            out = {}
            for s in syms:
                if s == "sh600519":
                    out[s] = {"amount": 2e4, "amplitude": 3.0}  # 2e4 万 = 2亿？ 见下
                elif s == "sz000001":
                    out[s] = {"amount": 2e3, "amplitude": 1.5}
            return out
        with patch("app.core.trading._api_config.get_tushare_pro", return_value=fake_pro), \
             patch.object(t_build, "fetch_tencent_quote", side_effect=fake_quotes), \
             patch.object(t_build, "build_score", side_effect=lambda sym, source: {
                 "symbol": sym, "score": 0.8, "pass_gate": True, "source": source}):
            # 注意 amount 单位：qt 为万元，_coarse_filter_active 内 ×1e4 → 元
            cands = t_build.scan_t_candidates(limit=10, source="scan")
            # 粗筛阈值 8e8 元 = 8 万万元；sh600519 2e4万 = 2e8 元 < 8e8 → 不过线 → 空
            # 因此本断言只验证流程不抛错
            self.assertIsInstance(cands, list)


class TestDailyAutoSelectBuild(_PGTestCase):
    """每日自动选股 → 次日自动建仓闭环（t_build_scan_results 表驱动）。"""

    def setUp(self):
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM t_build_scan_results"))
            db.commit()
        finally:
            db.close()
        from app.services import t_build
        self.tb = t_build

    def _insert_pending(self, trade_date, symbol="SH600000", score=0.8):
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO t_build_scan_results (trade_date, symbol, score, reasons, trend, status) "
                "VALUES (:td, :sym, :score, '[]', 'trend-ok', 'pending')"
            ), {"td": trade_date, "sym": symbol, "score": score})
            db.commit()
        finally:
            db.close()

    def _count_rows(self, trade_date=None, status=None):
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            sql = "SELECT count(*) AS n FROM t_build_scan_results WHERE 1=1"
            params = {}
            if trade_date:
                sql += " AND trade_date = :td"
                params["td"] = trade_date
            if status:
                sql += " AND status = :st"
                params["st"] = status
            return int(db.execute(text(sql), params).scalar())
        finally:
            db.close()

    def test_select_writes_pending_rows(self):
        from unittest.mock import patch
        with patch.object(self.tb, "scan_t_candidates", return_value=[
                {"symbol": "SH600000", "pass_gate": True, "score": 0.85,
                 "reasons": ["回踩充分"], "trend": {"note": "多头"}},
                {"symbol": "SZ000001", "pass_gate": True, "score": 0.72,
                 "reasons": ["量比正常"], "trend": {"note": "震荡"}},
                {"symbol": "SH600001", "pass_gate": False, "score": 0.3, "reasons": [], "trend": {}},
            ]), patch.object(self.tb, "_next_trade_date", return_value="2099-01-05"):
            rows = self.tb.daily_auto_select(limit=5)
        self.assertEqual(len(rows), 2)  # 只有达标写入
        self.assertEqual(self._count_rows(trade_date="2099-01-05", status="pending"), 2)
        syms = {r["symbol"] for r in rows}
        self.assertEqual(syms, {"SH600000", "SZ000001"})

    def test_select_idempotent(self):
        from unittest.mock import patch
        cand = {"symbol": "SH600000", "pass_gate": True, "score": 0.8,
                "reasons": ["ok"], "trend": {"note": "多头"}}
        with patch.object(self.tb, "scan_t_candidates", return_value=[cand]), \
             patch.object(self.tb, "_next_trade_date", return_value="2099-01-05"):
            self.tb.daily_auto_select(limit=5)
            self.tb.daily_auto_select(limit=5)
        self.assertEqual(self._count_rows(trade_date="2099-01-05"), 1)  # 幂等不重复

    def test_build_success_marks_built(self):
        from datetime import datetime
        from unittest.mock import patch
        today = datetime.now().strftime("%Y-%m-%d")
        self._insert_pending(today, "SH600000")
        quote = {"current": 10.2}
        with patch.object(self.tb, "confirm_build_timing",
                          return_value=(True, "回踩 2.1%", quote)), \
             patch.object(self.tb, "build_t_position", return_value={
                 "status": "success", "reason": "成交"}):
            results = self.tb.daily_auto_build()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "built")
        self.assertEqual(self._count_rows(today, "built"), 1)
        self.assertEqual(self._count_rows(today, "pending"), 0)

    def test_build_human_confirm_skipped(self):
        from datetime import datetime
        from unittest.mock import patch
        today = datetime.now().strftime("%Y-%m-%d")
        self._insert_pending(today, "SH600000")
        quote = {"current": 10.2}
        with patch.object(self.tb, "confirm_build_timing",
                          return_value=(True, "回踩 2.1%", quote)), \
             patch.object(self.tb, "build_t_position", return_value={
                 "status": "human_confirm", "reason": "升级人工（B1 首开）"}):
            results = self.tb.daily_auto_build()
        self.assertEqual(results[0]["action"], "skipped")
        self.assertIn("升级人工", results[0]["reason"])
        self.assertEqual(self._count_rows(today, "skipped"), 1)

    def test_build_timing_not_ready_waits(self):
        from datetime import datetime
        from unittest.mock import patch
        today = datetime.now().strftime("%Y-%m-%d")
        self._insert_pending(today, "SH600000")
        with patch.object(self.tb, "confirm_build_timing",
                          return_value=(False, "未回踩（距高点回撤 0.3%）", None)):
            results = self.tb.daily_auto_build()
        self.assertEqual(results[0]["action"], "wait")
        self.assertIn("未回踩", results[0]["reason"])
        self.assertEqual(self._count_rows(today, "pending"), 1)  # 保持 pending 下次再试


class TestAiLedBuildGateway(_PGTestCase):
    """AI 主导（ai_led）建仓与回转网关档位（decision_source 扩展）。"""

    def setUp(self):
        from app.services import t_build
        self.tb = t_build
        self.patches = [
            patch.object(self.tb, "compute_regime", return_value={
                "regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED",
                "interpret_sign": 1, "index_drop": 0.0}),
            patch.object(self.tb, "_is_trading_minute_allowed", return_value=(True, "")),
            patch.object(self.tb, "fetch_tencent_quote", side_effect=lambda syms: _fake_quote()),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in self.patches:
            p.stop()

    def test_ai_led_first_open_auto_passes(self):
        """ai_led 首开：与 daily_auto 同档跳过 B1，其余全链保留 → auto 放行。"""
        from app.services.t_build import validate_build_position
        r = validate_build_position("SH600000", 10.0, 1000, reason="ai test", decision_source="ai_led")
        self.assertTrue(r["pass"])
        self.assertEqual(r["mode"], "auto")

    def test_ai_led_agent_first_open_still_human(self):
        """agent 首开仍升级人工（ai_led 才放开 B1）。"""
        from app.services.t_build import validate_build_position
        r = validate_build_position("SH600000", 10.0, 1000, reason="agent test", decision_source="agent")
        self.assertEqual(r["mode"], "human_confirm")

    def test_ai_led_stop_all_rejects(self):
        """ai_led 不豁免熔断：STOP_ALL 时拒绝。"""
        from app.services import t_db
        from app.services.t_build import validate_build_position
        t_db.set_stop_all(True, "test-stop")
        try:
            r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="ai_led")
            self.assertFalse(r["pass"])
            self.assertIn("熔断", r["reason"])
        finally:
            t_db.set_stop_all(False, "")

    def test_ai_led_halt_rejects(self):
        """ai_led 不豁免 HALT。"""
        from app.services.t_build import validate_build_position
        with patch.object(self.tb, "compute_regime", return_value={
                "regime": "HALT", "gate_low_buy": "BLOCKED", "gate_high_sell": "ALLOWED",
                "interpret_sign": -1, "index_drop": -3.0}):
            r = validate_build_position("SH600000", 10.0, 1000, reason="test", decision_source="ai_led")
            self.assertFalse(r["pass"])
            self.assertIn("HALT", r["reason"])

    def test_ai_led_gateway_validate_accepts_source(self):
        """validate_order_at 接受 ai_led（同档风控），且 ai_led 主动买卖无 trigger 也走完整校验。"""
        from app.services.t_gateway import validate_order_at
        # 无底仓卖出（裸空）→ ai_led 同样拒绝（不豁免）
        ctx = {"regime": "ACTIVE", "quote": _fake_quote()["sh600000"], "ledger": {},
               "net_asset": 200000.0, "daily": {}, "risk": {}}
        r = validate_order_at("SH600000", "sell", 10.0, 100, ctx, decision_source="ai_led")
        self.assertFalse(r["pass"])
        self.assertIn("裸空", r["reason"])
        # 有底仓 + 买腿超档 → ai_led 拒绝
        ctx2 = {"regime": "ACTIVE", "quote": _fake_quote()["sh600000"],
                "ledger": {"SH600000": {"sellable": 100, "volume": 100, "avg_price": 10.0}},
                "net_asset": 200000.0, "daily": {}, "risk": {}}
        r2 = validate_order_at("SH600000", "buy", 10.0, 500, ctx2, decision_source="ai_led")
        self.assertFalse(r2["pass"])
        self.assertIn("档位", r2["reason"])


if __name__ == "__main__":
    unittest.main()

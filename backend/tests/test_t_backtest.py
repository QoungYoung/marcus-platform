# -*- coding: utf-8 -*-
"""做T回测测试（add-t-backtest-mode tasks 7.3）。

覆盖：
- 回放确定性（同数据两次运行事件流/指标一致）
- 账本 T+0 语义（高抛/低吸/次日结转/连亏跟踪）
- 撮合规则（无底仓低吸被拒、下一根 bar 成交）
- 防前视（量比基准只用 trade_date < 当日 的数据；regime L1 只用 T-1 及以前日线）
- LLM 复核注入（auto → 成交；human → 升级不成交）
- DB 链路（迁移表存在；任务创建/领取/落库/报告查询）——需本地 PostgreSQL，未启动自动跳过
- 沙盒隔离（bridge BACKTEST_DENY_TOOLS 覆盖全部生产写工具）
"""
import json
import os
import re
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
        print(f"[test-t-backtest] PostgreSQL 不可用，跳过 DB 用例: {e}")
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
        print(f"[test-t-backtest] 清理测试库失败（可忽略）: {e}")


class _PGTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not PG_AVAILABLE:
            raise unittest.SkipTest("需要本地 PostgreSQL")


# ────────────────────────────────────────────────────────────────
# 假数据生成（无网络）
# ────────────────────────────────────────────────────────────────

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
    """落假数据到临时缓存目录，返回 (cache_dir, symbol)。"""
    symbol = "TEST01"
    m5_all = _make_day(days[0], 10.0, -0.3) + _make_day(days[1], 9.9, 0.1) + _make_day(days[2], 10.0, 0.2)
    index_m5 = _make_day(days[0], 3000.0, 0.0, 50000) + _make_day(days[1], 3000.0, 0.0, 50000) + _make_day(days[2], 3000.0, 0.0, 50000)
    index_daily = [
        {"trade_date": d, "open": 3000.0, "close": 3000.0 + (i * 2), "high": 3010.0, "low": 2990.0, "vol": 1e6}
        for i, d in enumerate(days)
    ]
    tmp = Path(tempfile.mkdtemp(prefix="tbt_test_"))
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


def _low_buy_task(symbol: str, target: float = 9.9):
    return {
        "symbol": symbol, "init_shares": 1000, "init_price": 10.0, "net_asset": 200000.0,
        "conditions": [{
            "id": 1, "trigger_kind": "low_buy", "target_price": target,
            "vol_ratio_thresh": 0.0, "stabilize_level": "not_new_low",
            "armed": 1, "status": "active",
        }],
    }


# ────────────────────────────────────────────────────────────────
# 引擎核心（无 DB）
# ────────────────────────────────────────────────────────────────

class TestEngineCore(unittest.TestCase):
    def setUp(self):
        self.cache, self.symbol = _make_fixture()
        self.task = _low_buy_task(self.symbol)

    def test_replay_deterministic(self):
        from app.services.t_backtest import TBacktestEngine
        r1 = TBacktestEngine(self.task, str(self.cache)).run()
        r2 = TBacktestEngine(self.task, str(self.cache)).run()
        self.assertEqual(r1["status"], "completed")
        self.assertEqual(json.dumps(r1["events"]), json.dumps(r2["events"]))
        self.assertEqual(r1["metrics"], r2["metrics"])

    def test_trigger_and_execute(self):
        from app.services.t_backtest import TBacktestEngine
        r = TBacktestEngine(self.task, str(self.cache)).run()
        self.assertGreater(r["metrics"]["trigger_count"], 0, "历史低价应触发低吸")
        self.assertGreater(r["metrics"]["executed_count"], 0, "auto 复核应成交")
        # 低吸加仓次数上限（≤2 次/日，P0 风控）会拦截第 3 笔起——执行率不再强制 100%
        blocked = [e for e in r["events"] if e.get("type") == "blocked"]
        self.assertTrue(any("低吸加仓次数超限" in str(e.get("data", {}).get("reason", "")) for e in blocked),
                        "第 3 笔低吸应被加仓次数上限拦截")

    def test_no_trigger_without_price_hit(self):
        from app.services.t_backtest import TBacktestEngine
        task = _low_buy_task(self.symbol, target=8.0)  # 目标价 8.0，历史最低 ~9.7，不应触发
        r = TBacktestEngine(task, str(self.cache)).run()
        self.assertEqual(r["metrics"]["trigger_count"], 0)

    def test_llm_review_human_blocks(self):
        """LLM 复核（旧语义 decision=human → wait 保守）→ 不成交。"""
        from app.services.t_backtest import TBacktestEngine
        calls = {"n": 0}

        def review_fn(rev_ctx):
            calls["n"] += 1
            return {"decision": "human", "reason": "量价存疑"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        self.assertGreater(calls["n"], 0, "LLM 复核应被调用")
        self.assertEqual(r["metrics"]["executed_count"], 0, "human 决策不成交")
        self.assertGreaterEqual(r["metrics"]["ai_wait_count"] + r["metrics"]["ai_abandon_count"], 0)

    def test_llm_review_auto_executes(self):
        from app.services.t_backtest import TBacktestEngine

        def review_fn(rev_ctx):
            return {"decision": "auto", "reason": "量价合理"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        self.assertGreater(r["metrics"]["executed_count"], 0)

    def test_ai_review_action_exec(self):
        """AI 决策 exec → 撮合成交。"""
        from app.services.t_backtest import TBacktestEngine

        def review_fn(rev_ctx):
            return {"action": "exec", "reason": "回踩到位"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        self.assertGreater(r["metrics"]["executed_count"], 0)
        reviews = [e for e in r["events"] if e.get("type") == "review"]
        self.assertTrue(all(e["data"].get("action") == "exec" for e in reviews))

    def test_ai_outcomes_computed_after_fill(self):
        """回测 outcome：成交后计算（防前视：只用成交 bar 之后），exec 胜率入 metrics。"""
        from app.services.t_backtest import TBacktestEngine

        def review_fn(rev_ctx):
            return {"action": "exec", "reason": "回踩到位"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        outcomes = r.get("ai_outcomes", [])
        self.assertGreater(len(outcomes), 0, "成交后应有 outcome")
        oc = outcomes[0]
        # 防前视：outcome 只用成交后的 bar（bars_after ≥ 3）
        self.assertGreaterEqual(oc.get("bars_after", 0), 3)
        self.assertIn(oc.get("direction"), ("up", "down"))
        self.assertIn("pct_change", oc)
        self.assertIn("fill_price", oc)
        # metrics 含 exec 胜率（值可为 None 若方向归一后无正负，但 count>0）
        self.assertGreater(r["metrics"].get("ai_exec_count", 0), 0)
        self.assertIn("ai_exec_win_rate_pct", r["metrics"])

    def test_ai_review_action_wait(self):
        """AI 决策 wait → 记事件不撮合，ai_wait_count 计数。"""
        from app.services.t_backtest import TBacktestEngine

        def review_fn(rev_ctx):
            return {"action": "wait", "reason": "量比不足"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        self.assertEqual(r["metrics"]["executed_count"], 0)
        self.assertGreater(r["metrics"]["ai_wait_count"], 0)
        waits = [e for e in r["events"] if e.get("type") == "ai_wait"]
        self.assertGreater(len(waits), 0)

    def test_ai_review_action_abandon(self):
        """AI 决策 abandon → 记放弃事件不成交，ai_abandon_count 计数。"""
        from app.services.t_backtest import TBacktestEngine

        def review_fn(rev_ctx):
            return {"action": "abandon", "reason": "追高"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        self.assertEqual(r["metrics"]["executed_count"], 0)
        self.assertGreater(r["metrics"]["ai_abandon_count"], 0)
        self.assertGreaterEqual(r["metrics"]["escalated_human_count"], 0)

    def test_no_lookahead_vol_base(self):
        from app.services.t_backtest import compute_vol_ratio_base_up_to
        from app.services.t_backtest_data import load_m5
        m5 = load_m5(self.symbol, self.cache)
        base = compute_vol_ratio_base_up_to(m5, "20260810")
        self.assertEqual(base, {}, "首日量比基准必须为空（无历史交易日数据）")
        base2 = compute_vol_ratio_base_up_to(m5, "20260811")
        self.assertGreater(len(base2), 0, "次日量比基准使用首日数据")
        for k, v in base2.items():
            self.assertGreater(v, 0)

    def test_ledger_t0_roundtrip(self):
        from app.services.t_backtest import TBacktestLedger
        ledger = TBacktestLedger("X", 1000, 10.0, 200000.0)
        ledger.do_sell(10.5, 300)
        self.assertEqual(ledger.sellable(), 700)
        ledger.do_buy(10.0, 300)
        self.assertEqual(ledger.total_shares(), 1000)
        ledger.end_of_day()
        self.assertEqual(ledger.base_shares, 1000)
        self.assertEqual(ledger.bought_today, 0)
        self.assertGreater(ledger.realized_pnl, 0)

    def test_ledger_cost_not_inflated_after_roundtrip(self):
        """高抛卖+买回闭环后成本不应虚高（已卖部分实现盈亏不结转成本）。

        回归：旧公式 (old_cost×base + buy_amount)/new_base 在卖出日把成本抬高，
        200股@37.03 高抛卖100@39.4 买回100@38.6 被算成 56.35（应 ~37.8）。
        """
        from app.services.t_backtest import TBacktestLedger
        ledger = TBacktestLedger("X", 200, 37.03, 200000.0)
        ledger.do_sell(39.4, 100)   # 高抛 100
        ledger.do_buy(38.6, 100)    # 买回 100
        ledger.end_of_day()
        self.assertEqual(ledger.base_shares, 200)
        # 新成本 = (37.03×100 + 38.6×100) / 200 ≈ 37.82（不得虚高到 50+）
        self.assertLess(ledger.cost_price, 40.0, f"成本被虚高: {ledger.cost_price}")
        self.assertAlmostEqual(ledger.cost_price, (37.03 * 100 + 38.6 * 100) / 200, delta=0.05)

    def test_ledger_consecutive_losses(self):
        from app.services.t_backtest import TBacktestLedger
        ledger = TBacktestLedger("X", 1000, 10.0, 200000.0)
        ledger.do_sell(9.5, 100)   # 亏
        self.assertEqual(ledger.consecutive_losses, 1)
        ledger.do_sell(9.0, 100)   # 再亏
        self.assertEqual(ledger.consecutive_losses, 2)
        ledger.do_sell(11.0, 100)  # 盈 → 清零
        self.assertEqual(ledger.consecutive_losses, 0)

    def test_no_sellable_buy_rejected(self):
        """无底仓标的低吸在复核层被拒（_rule_review → abandon，对齐网关硬闸门）。"""
        from app.services.t_backtest import TBacktestEngine, TBacktestLedger, _rule_review
        ledger = TBacktestLedger("X", 0, 10.0, 200000.0)  # 无底仓
        action, reason = _rule_review(
            {"event_type": "low_buy", "symbol": "X"}, {"regime": "ACTIVE"}, ledger)
        self.assertEqual(action, "abandon")
        self.assertIn("无底仓", reason)

    def test_zero_share_sell_blocked(self):
        """底仓耗尽后卖腿预拦截：无底仓时不触发高抛（不产生无意义成交/刷屏）。"""
        from app.services.t_backtest import TBacktestEngine
        # 100 股底仓 + 宽松高抛阈值（首日必触发，卖光后无底仓预拦截不再触发）
        high_target = 9.0
        task = {
            "symbol": self.symbol, "init_shares": 100, "init_price": 10.0,
            "net_asset": 200000.0,
            "conditions": [{
                "id": 2, "trigger_kind": "high_sell_then_buy_back",
                "sell_target_price": high_target, "vol_ratio_thresh": 0.0, "armed": 1,
            }],
        }
        r = TBacktestEngine(task, str(self.cache)).run()
        trades = [e["data"]["trade"] for e in r["events"] if e.get("type") == "trade" and e.get("data", {}).get("trade")]
        self.assertTrue(all(t["volume"] > 0 for t in trades), "不应存在 0 股成交")
        # 底仓耗尽后（卖光）无触发——不再有无意义的高抛触发事件
        sells = [t for t in trades if t["side"] == "sell"]
        self.assertGreater(len(sells), 0, "应有高抛成交")
        self.assertLessEqual(len(sells), 1, "卖光后不应继续触发高抛（预拦截）")

    def test_buyback_after_sell_goes_through_gateway(self):
        """high_sell_then_buy_back 成交后挂买回单，买回腿走网关（无底仓被拒 = 风控生效）。"""
        from app.services.t_backtest import TBacktestEngine
        task = {
            "symbol": self.symbol, "init_shares": 100, "init_price": 10.0,
            "net_asset": 200000.0,
            "conditions": [{
                "id": 2, "trigger_kind": "high_sell_then_buy_back",
                "sell_target_price": 9.0, "vol_ratio_thresh": 0.0, "armed": 1,
            }],
        }
        r = TBacktestEngine(task, str(self.cache)).run()
        trades = [e for e in r["events"] if e.get("type") == "trade"]
        buybacks = [t for t in trades if t.get("data", {}).get("trigger", {}).get("event_type") == "buyback"]
        # 高抛成交存在
        self.assertTrue(any(t["data"]["trade"]["side"] == "sell" for t in trades), "应有高抛成交")
        # 买回要么成交要么被网关拒绝（记录事件），不能静默消失
        blocked = [e for e in r["events"] if e.get("type") == "blocked"]
        buyback_blocked = [b for b in blocked if "买回" in str(b.get("data", {}).get("reason", ""))]
        self.assertTrue(len(buybacks) > 0 or len(buyback_blocked) > 0,
                        "买回腿必须走网关（成交或被拒），不得静默")

    def test_ai_update_condition_applies(self):
        """AI update_condition → 条件目标价更新（当日剩余 bar 与后续交易日生效）→ 事件记录。"""
        from app.services.t_backtest import TBacktestEngine
        calls = {"n": 0}

        def review_fn(rev_ctx):
            calls["n"] += 1
            # 首次触发给 update_condition（把低吸目标价拉高），后续 exec
            if calls["n"] == 1:
                return {"action": "update_condition", "reason": "目标价过低够不着",
                        "condition": {"trigger_kind": "low_buy", "target_price": 10.1,
                                      "stop_loss_price": 9.2}}
            return {"action": "exec", "reason": "到位"}

        task = {
            "symbol": self.symbol, "init_shares": 1000, "init_price": 10.0,
            "net_asset": 200000.0,
            "conditions": [{
                "id": 1, "trigger_kind": "low_buy", "target_price": 9.9,
                "stop_loss_price": 9.0, "vol_ratio_thresh": 0.0, "armed": 1,
            }],
        }
        r = TBacktestEngine(task, str(self.cache), review_fn=review_fn).run()
        updates = [e for e in r["events"] if e.get("type") == "condition_update"]
        self.assertGreater(len(updates), 0, "AI update_condition 应记录事件")
        self.assertGreaterEqual(r["metrics"].get("ai_condition_update_count", 0), 1)
        # 条件更新后仍可 exec（update 不撮合，但后续触发正常撮合）
        self.assertGreater(r["metrics"]["executed_count"], 0, "更新后触发应可成交")

    def test_ai_update_condition_missing_cond_conservative(self):
        """update_condition 缺 condition → 保守等待（不崩溃、不撮合）。"""
        from app.services.t_backtest import TBacktestEngine

        def review_fn(rev_ctx):
            return {"action": "update_condition", "reason": "无 condition"}

        r = TBacktestEngine(self.task, str(self.cache), review_fn=review_fn).run()
        self.assertEqual(r["metrics"]["executed_count"], 0)
        self.assertGreaterEqual(r["metrics"].get("ai_condition_update_count", 0), 0)

    def test_gen_t_conditions_ai_fallback_rule(self):
        """LLM 模式 AI 条件生成：bridge 不可达（返回 None）→ 回退规则公式（结构完整）。"""
        from app.services.t_backtest import _gen_t_conditions
        with patch("app.services.t_bridge.generate_conditions", return_value=None):
            conds = _gen_t_conditions(review_fn=lambda x: {}, symbol="TEST", price=10.0,
                                      amp_med=5.0)
        kinds = {c["trigger_kind"] for c in conds}
        self.assertIn("low_buy", kinds)
        self.assertIn("high_sell_then_buy_back", kinds)
        for c in conds:
            self.assertGreater(c["target_price"], 0)
            self.assertIn("stop_loss_price", c)
        # 低吸 < 成本 < 高抛
        low = next(c for c in conds if c["trigger_kind"] == "low_buy")
        high = next(c for c in conds if c["trigger_kind"] == "high_sell_then_buy_back")
        self.assertLess(low["target_price"], 10.0)
        self.assertGreater(high["target_price"], 10.0)

    def test_gen_t_conditions_ai_result_used(self):
        """LLM 模式 AI 条件生成：bridge 返回 AI 条件 → 直接采用（并补齐 stop_loss_price）。"""
        from app.services.t_backtest import _gen_t_conditions
        ai_conds = [
            {"trigger_kind": "low_buy", "target_price": 9.5, "sell_target_price": 10.3,
             "vol_ratio_thresh": 1.5, "stabilize_level": "not_new_low"},
            {"trigger_kind": "high_sell_then_buy_back", "target_price": 10.6,
             "sell_target_price": 10.6, "vol_ratio_thresh": 1.2},
        ]
        with patch("app.services.t_bridge.generate_conditions",
                   return_value={"conditions": ai_conds, "source": "ai", "reason": "AI 生成"}):
            conds = _gen_t_conditions(review_fn=lambda x: {}, symbol="TEST", price=10.0,
                                      amp_med=5.0)
        self.assertEqual(len(conds), 2)
        # AI 漏给 stop_loss_price → 按规则补齐
        self.assertTrue(all(c.get("stop_loss_price", 0) > 0 for c in conds),
                        "AI 条件缺失止损价应补齐")

    def test_gen_t_conditions_rule_mode_no_ai_call(self):
        """规则模式（review_fn=None）→ 不调用 bridge，直接用规则公式。"""
        from app.services.t_backtest import _gen_t_conditions
        with patch("app.services.t_bridge.generate_conditions") as mock_gen:
            conds = _gen_t_conditions(review_fn=None, symbol="TEST", price=10.0, amp_med=5.0)
            mock_gen.assert_not_called()
        self.assertEqual(len(conds), 2)


# ────────────────────────────────────────────────────────────────
# DB 链路（需 PostgreSQL）
# ────────────────────────────────────────────────────────────────

class TestBacktestDB(_PGTestCase):
    def test_migration_tables(self):
        from sqlalchemy import inspect
        from app.database import engine
        tables = set(inspect(engine).get_table_names())
        for t in ("t_backtest_tasks", "t_backtest_events", "t_backtest_trades",
                  "t_backtest_equity_snapshots", "t_backtest_metrics"):
            self.assertIn(t, tables, f"缺表 {t}")

    def test_task_lifecycle_and_persistence(self):
        from app.services import t_backtest_runner as runner
        task_id = runner.create_task(
            symbol="TEST01", start_date="2026-08-10", end_date="2026-08-12",
            conditions=[{"trigger_kind": "low_buy", "target_price": 9.9, "armed": 1}],
            init_shares=1000, review_mode="rule",
        )
        self.assertIsNotNone(task_id)
        task = runner.get_task(task_id)
        self.assertEqual(task["status"], "pending")
        # 领取（模拟 worker）
        claimed = runner.claim_pending_task(consumer="test")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["status"], "running")
        # 落库（喂假结果，验证持久化链路）
        runner.save_events(task_id, [{"type": "trigger", "trade_day": "20260810", "data": {"bar_time": "2026-08-10 10:00:00"}}])
        runner.save_trades(task_id, [{"symbol": "TEST01", "side": "buy", "price": 9.9, "volume": 300, "realized_pnl": 0.0, "fees": 3.0}])
        runner.save_equity(task_id, [{"trade_date": "2026-08-10", "total_asset": 200000.0, "realized_pnl": 0.0, "position": 1000, "close": 10.0}])
        runner.save_metrics(task_id, {"total_return_pct": 0.5}, ["口径1"])
        runner.update_task_status(task_id, "completed", progress=100)
        # 报告可查
        m = runner.get_metrics(task_id)
        self.assertIsNotNone(m)
        self.assertEqual(m["metrics"]["total_return_pct"], 0.5)
        # 重复领取被拒（FOR UPDATE SKIP LOCKED 原子性：completed 不再可领）
        self.assertIsNone(runner.claim_pending_task(consumer="test2"))
        # 清理
        self._cleanup(task_id)

    def _cleanup(self, task_id):
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            for tbl in ("t_backtest_events", "t_backtest_trades",
                        "t_backtest_equity_snapshots", "t_backtest_metrics",
                        "t_backtest_tasks"):
                if tbl == "t_backtest_tasks":
                    db.execute(text("DELETE FROM t_backtest_tasks WHERE id = :id"), {"id": task_id})
                else:
                    db.execute(text(f"DELETE FROM {tbl} WHERE task_id = :id"), {"id": task_id})
            db.commit()
        finally:
            db.close()


# ────────────────────────────────────────────────────────────────
# 沙盒隔离断言（bridge JS 静态检查）
# ────────────────────────────────────────────────────────────────

class TestSandboxIsolation(unittest.TestCase):
    def test_bridge_deny_covers_all_production_write_tools(self):
        bridge_src = (REPO_ROOT / "docker" / "dsh" / "bridge" / "lib" / "index.js").read_text(encoding="utf-8")
        m = re.search(r"const BACKTEST_DENY_TOOLS = \[(.*?)\];", bridge_src, re.S)
        self.assertIsNotNone(m, "bridge 缺少 BACKTEST_DENY_TOOLS")
        denied = set(re.findall(r"'([^']+)'", m.group(1)))
        production_writes = {
            "place_order", "cancel_order", "calc_position",
            "update_golden_pit_etf_config", "create_t_condition",
            "list_t_fields", "list_t_conditions", "run_t_backtest",
        }
        self.assertTrue(
            production_writes.issubset(denied),
            f"生产写工具未被全部 deny: {production_writes - denied}",
        )
        # 回测复核会话 key 与生产会话隔离
        self.assertIn("t-backtest-", bridge_src, "回测会话 key 前缀缺失")


if __name__ == "__main__":
    unittest.main()

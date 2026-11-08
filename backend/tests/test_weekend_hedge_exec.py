# -*- coding: utf-8 -*-
"""周末/长假前避险的**执行层**（`WOLF_WEEKEND_HEDGE_EXEC`，默认关）单测。

语料（2026-08-21 14:20）：「2点半 如果还是缩量 还是不拉升 我会先把这两天T进去的仓位出来一半
防止周末出利空 这样周一再拿回来。出于仓位安全考虑 65%仓位过周末」。
现状问题：原模块只到**提示层**（`directive()` 注入纪律上下文），**没有执行动作** ⇒ 本开关补执行层。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_weekend_hedge as W  # noqa: E402


class WeekendHedgeExecTest(unittest.TestCase):
    def _res(self, active=True):
        return {"active": active, "target_pct": 0.65, "reason": "缩量∧未拉升"}

    def test_default_off_no_action(self):
        r = W.exec_plan(self._res(), {"SZ002429": 1000}, enabled=False)
        self.assertFalse(r["active"])
        self.assertEqual(r["sells"], [])
        self.assertFalse(r["block_new_buys"])

    def test_active_sells_half_of_t_added(self):
        r = W.exec_plan(self._res(), {"SZ002429": 1000, "SZ002195": 300}, enabled=True)
        self.assertTrue(r["active"])
        self.assertIn(("SZ002429", 500), r["sells"])
        self.assertIn(("SZ002195", 100), r["sells"])
        self.assertTrue(r["block_new_buys"], "避险当日不得新开仓/加仓")
        self.assertAlmostEqual(r["target_pct"], 0.65)

    def test_inactive_when_conditions_not_met(self):
        r = W.exec_plan(self._res(False), {"SZ002429": 1000}, enabled=True)
        self.assertFalse(r["active"])
        self.assertEqual(r["sells"], [])

    def test_lot_alignment(self):
        r = W.exec_plan(self._res(), {"SZ002429": 150}, lot=100, enabled=True)
        for _s, v in r["sells"]:
            self.assertEqual(v % 100, 0)
            self.assertGreater(v, 0)

    def test_switch_default_off(self):
        with mock.patch.dict(os.environ, {"WOLF_WEEKEND_HEDGE_EXEC": ""}, clear=False):
            self.assertFalse(W.exec_enabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WeekendHedgeWiringTest(unittest.TestCase):
    """挂接完成性断言（防"模块写了没挂上"）。"""

    def test_monitor_wired(self):
        import inspect
        import app.services.t_monitor as TM
        src = inspect.getsource(TM)
        for marker in ("WH_EXEC", "_wh_plan_today", "weekend_hedge", "_WH_STATS"):
            self.assertIn(marker, src, "缺少挂接标记 %s" % marker)

    def test_block_flag_used_in_buy_gate(self):
        import inspect
        import app.services.t_monitor as TM
        src = inspect.getsource(TM.TMonitor._round) if hasattr(TM.TMonitor, "_round") else inspect.getsource(TM)
        self.assertIn("_WH_STATE.get(\"block\")", src, "买腿门未读取避险封禁标记")

    def test_switch_default_off_and_stats_exposed(self):
        import app.services.t_monitor as TM
        self.assertFalse(TM.WH_EXEC, "默认必须关")
        self.assertIn("weekend_hedge", TM.round_timing())


def _db_ok():
    try:
        import os
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""))
        conn.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_db_ok(), "需要本地 PG（DATABASE_URL）")
class MarketAmountUnitTest(unittest.TestCase):
    """单位与量能回落（2026-09-18 修）：tushare `amount` **单位＝千元**，不是元。"""

    def test_unit_is_thousand_yuan(self):
        raw = W.market_amount("20260109")
        self.assertIsNotNone(raw)
        self.assertAlmostEqual(raw, 3152368298.0, delta=1e6,
                               msg="20260109 全市场成交额 ≈ 3.152e9 **千元**")

    def test_yi_helper(self):
        yi = W.market_amount_yi("20260109")
        self.assertAlmostEqual(yi, 31524.0, delta=20.0, msg="≈ 31,524 亿元（= 3.15 万亿）")

    def test_ratio_not_none_in_replay(self):
        """**回归断言**：回放窗口里缩量比必须算得出来（否则规则静默沉默）。"""
        for d, p in (("20260109", "20260108"), ("20260116", "20260115"),
                     ("20260123", "20260122"), ("20260130", "20260129")):
            r, src = W.effective_shrink_ratio(d, p)
            self.assertIsNotNone(r, "%s 缩量比为 None" % d)
            self.assertEqual(src, "market_amount", "%s 应回落到市场成交额" % d)

    def test_jan30_shrink_detected(self):
        r, _ = W.effective_shrink_ratio("20260130", "20260129")
        self.assertLess(r, 1.0, "2026-01-30 缩量（比 0.8782）")
        self.assertTrue(W.still_shrinking(r))

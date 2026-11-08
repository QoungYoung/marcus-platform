# -*- coding: utf-8 -*-
"""L3 v1 第二半回归：**正T买当日累计额度**（`WOLF_ZT_DAY_BUDGET`，库内默认 0=关）。

语料（逐字）2026-03-31「必须是有底仓、自己一直关注的方向（没有底仓的不做）；**只用10%仓位做T**」。
与 ③（单笔 ≤10%）的区别：③ 管单笔，本额度管**当日累计**。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")

import app.services.t_gateway as gw  # noqa: E402


class ZtDayBudgetTest(unittest.TestCase):
    def test_default_off(self):
        self.assertEqual(gw.zt_day_budget_pct(), 0.0)
        v, why = gw.clamp_zt_day_budget(1000, 40.0, 250000.0, 0.0)
        self.assertEqual(v, 1000)
        self.assertIn("未开", why)

    def test_within_budget(self):
        v, why = gw.clamp_zt_day_budget(500, 40.0, 250000.0, 0.0, pct=10.0)
        self.assertEqual(v, 500, "2 万 < 2.5 万额度 ⇒ 原样")

    def test_clamped_by_remaining(self):
        """额度 2.5 万、已用 2 万 ⇒ 剩 5,000 ⇒ 40 元股价只能买 100 股。"""
        v, why = gw.clamp_zt_day_budget(1000, 40.0, 250000.0, 20000.0, pct=10.0)
        self.assertEqual(v, 100, "剩余 5,000 元 / 40 元 → 100 股（整手）")
        self.assertIn("当日正T额度", why)

    def test_exhausted(self):
        v, _why = gw.clamp_zt_day_budget(1000, 40.0, 250000.0, 26000.0, pct=10.0)
        self.assertEqual(v, 0, "额度用尽 ⇒ 0 股（调用方拒绝该腿）")

    def test_failopen_no_equity(self):
        v, why = gw.clamp_zt_day_budget(1000, 40.0, None, 0.0, pct=10.0)
        self.assertEqual(v, 1000, "权益不可用 ⇒ 不介入")
        self.assertIn("不可用", why)

    def test_accumulator(self):
        gw._ZT_USED.clear()
        gw.zt_add_used("drabx", "20260105", 12345.0)
        gw.zt_add_used("drabx", "20260105", 55.0)
        self.assertAlmostEqual(gw.zt_used_today("drabx", "20260105"), 12400.0)
        self.assertEqual(gw.zt_used_today("drabx", "20260106"), 0.0, "跨日清零")

    def test_wiring_and_pins(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("def clamp_zt_day_budget(", src)
        self.assertIn("def zt_add_used(", src)
        self.assertIn("_v_b2, _why_b2 = clamp_zt_day_budget(", src)
        self.assertIn("zt_add_used(account_id, _day_ok, _amt_ok)", src)
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_ZT_DAY_BUDGET", pins)


if __name__ == "__main__":
    unittest.main()

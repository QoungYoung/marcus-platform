# -*- coding: utf-8 -*-
"""低吸腿当日额度回归（账本 §9.70/§9.71）。

依据：四臂真实成交 FIFO 归因 —— 低吸(253/254) 264 笔、名义占 53%、已实现 −0.51%/名义；
趋势突破腿 70 笔、+5.75%。把额度从低吸挪到趋势突破：已实现 +67k → +231k（单位 0.54% → 2.12%）。
本开关只**压低吸腿当日总量**（不抬其它腿），库内默认 0 = 逐字旧行为。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")

from app.services import t_gateway as gw  # noqa: E402


class LowdipBudgetTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("WOLF_LOWDIP_DAY_BUDGET")
        os.environ.pop("WOLF_LOWDIP_DAY_BUDGET", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("WOLF_LOWDIP_DAY_BUDGET", None)
        else:
            os.environ["WOLF_LOWDIP_DAY_BUDGET"] = self._old

    def test_default_off(self):
        self.assertEqual(gw.lowdip_day_budget_pct(), 0.0)
        v, why = gw.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 0.0)
        self.assertEqual(v, 1000)
        self.assertIn("未开", why)

    def test_clamp_within_and_beyond(self):
        os.environ["WOLF_LOWDIP_DAY_BUDGET"] = "25"          # 25% × 250,000 = 62,500
        self.assertEqual(gw.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 0.0)[0], 1000)   # 20,000 < 额度
        self.assertEqual(gw.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 55000.0)[0], 300,
                         "剩 7,500 / 20 = 375 → 整手 300")
        self.assertEqual(gw.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 62500.0)[0], 0, "额度用尽")
        self.assertEqual(gw.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 99999.0)[0], 0)

    def test_failopen_without_equity(self):
        os.environ["WOLF_LOWDIP_DAY_BUDGET"] = "25"
        v, why = gw.clamp_lowdip_day_budget(1000, 20.0, None, 0.0)
        self.assertEqual(v, 1000, "权益取不到 ⇒ 不介入（fail-open）")
        self.assertIn("权益", why)

    def test_kind_classification(self):
        for k in ("custom_prevlow", "custom_m5dump", "low_buy", "wolf_253_build", "wolf_254_refill"):
            self.assertTrue(gw.is_lowdip_kind(k), k)
        for k in ("trend_break_buy", "wolf_zheng_t_buy", "custom_buy", "", None):
            self.assertFalse(gw.is_lowdip_kind(k), str(k))

    def test_accumulator(self):
        gw.lowdip_add_used("t_acc_test", "20260105", 1000)
        gw.lowdip_add_used("t_acc_test", "20260105", 500)
        self.assertAlmostEqual(gw.lowdip_used_today("t_acc_test", "20260105"), 1500.0)
        self.assertAlmostEqual(gw.lowdip_used_today("t_acc_test", "20260106"), 0.0, msg="按日隔离")

    def test_wiring_and_pins(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("clamp_lowdip_day_budget(", src)
        self.assertIn("lowdip_add_used(", src)
        self.assertIn("account_equity(account_id, get_sellable_ledger(account_id)", src,
                      "权益必须走 account_equity（否则静默 fail-open）")
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_LOWDIP_DAY_BUDGET", pins)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""换手治理单测（语料：不横跳＝第一纪律；反对无逻辑来回，不禁止换票）。"""
import os
import unittest
from unittest import mock

from app.services import t_turnover as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PureTest(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(T.GUARD_ON)
        src = open(os.path.join(ROOT, "app", "services", "t_turnover.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_TURNOVER_GUARD", "0")', src)

    def test_window(self):
        self.assertTrue(T.in_t_window("09:45"))
        self.assertTrue(T.in_t_window("09:59"))
        self.assertTrue(T.in_t_window("14:05"))
        self.assertFalse(T.in_t_window("11:00"))
        self.assertFalse(T.in_t_window("13:30"))

    def test_churn_same_price_reverse(self):
        hit, why = T.churn_hit("卖出", 19.0, "买入", 19.05, 5)
        self.assertTrue(hit)
        self.assertIn("同价来回", why)
        self.assertFalse(T.churn_hit("卖出", 19.0, "买入", 19.5, 5)[0])
        self.assertFalse(T.churn_hit("卖出", 19.0, "买入", 19.02, 40)[0])
        self.assertFalse(T.churn_hit("买入", 19.0, "买入", 19.0, 1)[0])

    def test_refill_gate(self):
        self.assertTrue(T.refill_blocked(19.0, 18.8)[0])
        self.assertFalse(T.refill_blocked(19.0, 19.2)[0])

    def test_protective_sell_always_passes(self):
        with mock.patch.object(T, "GUARD_ON", True):
            ok, _ = T.check("SZ000001", "sell", 10.0, "x", reason="止损离场", is_stop_loss=True)
            self.assertTrue(ok)


class WiringTest(unittest.TestCase):
    def test_gateway_hook(self):
        src = open(os.path.join(ROOT, "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("from app.services import t_turnover as _tw", src)
        self.assertIn('level": "TURNOVER"', src)

    def test_driver_enables(self):
        src = open(os.path.join(ROOT, "..", "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        self.assertIn('("WOLF_TURNOVER_GUARD", "1")', src)


if __name__ == "__main__":
    unittest.main()

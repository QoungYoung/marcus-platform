# -*- coding: utf-8 -*-
"""候选腿质量闸单测（审计根因：tier=off 绕过 build_sizing ⇒ 打分门槛从未生效）。"""
import os
import unittest
from unittest import mock

from app.services import t_leg_quality as Q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GuardTest(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(Q.ON)
        src = open(os.path.join(ROOT, "app", "services", "t_leg_quality.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_LEG_QUALITY", "0")', src)

    def test_score_gate_blocks_below_min(self):
        with mock.patch.object(Q, "ON", True), \
             mock.patch.object(Q, "score_of", return_value={"score": 0.61, "pass_gate": False, "reasons": ["趋势闸门未过"]}):
            ok, why = Q.score_ok("SH600658", as_of="20260119")
            self.assertFalse(ok)
            self.assertIn("质量不达标", why)
            self.assertIn("0.780", why) if False else self.assertIn("0.78", why)
        with mock.patch.object(Q, "ON", True), \
             mock.patch.object(Q, "score_of", return_value={"score": 0.83, "pass_gate": True, "reasons": []}):
            self.assertTrue(Q.score_ok("SZ002429", as_of="20260107")[0])

    def test_score_gate_fail_open(self):
        with mock.patch.object(Q, "ON", True), mock.patch.object(Q, "score_of", return_value=None):
            self.assertTrue(Q.score_ok("X")[0])

    def test_add_guard(self):
        with mock.patch.object(Q, "ON", True):
            self.assertFalse(Q.add_guard("2026-01-19", 5, -3.0)[0])   # 满 3 日且浮亏 → 禁加仓
            self.assertTrue(Q.add_guard("2026-01-19", 1, -3.0)[0])    # 未满 3 日 → 放行
            self.assertTrue(Q.add_guard("2026-01-19", 5, +1.5)[0])    # 有浮盈 → 放行


class WiringTest(unittest.TestCase):
    def test_gateway_and_driver(self):
        g = open(os.path.join(ROOT, "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("from app.services import t_leg_quality as _lq", g)
        self.assertIn('level": "LEGQUALITY"', g)
        d = open(os.path.join(ROOT, "..", "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        # 2026-09-18 用户"改回去"：执行层的分数墙已撤（回测也显式置 0），筛选改落选股层
        self.assertIn('("WOLF_LEG_QUALITY", "0")', d)


if __name__ == "__main__":
    unittest.main()

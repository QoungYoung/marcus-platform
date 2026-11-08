# -*- coding: utf-8 -*-
"""lowdip_junk_gate 单测：低吸腿「窄杂毛门」= 业绩 bad ∧ PE>300（交集）。

依据（台账「续5」）：影响面 4%、剔除集净 −9,216（剔除会赚）、6/8 臂改善；命中票天津普林/国星光电/格尔软件。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import lowdip_junk_gate as G  # noqa: E402


class TestSwitchAndParams(unittest.TestCase):
    def setUp(self):
        for k in ("WOLF_LOWDIP_JUNK_GATE", "WOLF_LOWDIP_JUNK_MAX_PE", "WOLF_LOWDIP_JUNK_EARN",
                  "WOLF_FUND_TAG", "SZ_TAG"):
            os.environ.pop(k, None)

    def test_default_off(self):
        self.assertFalse(G.enabled(), "库内默认关 ⇒ 生产零影响")
        self.assertFalse(G.blocked("SZ002134", day="20260113")[0], "关着时一律放行")
        kept, dropped = G.filter_symbols(["SZ002134", "SH603936"], day="20260113")
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, {})

    def test_params(self):
        self.assertEqual(G.max_pe(), 300.0, "默认阈值 = 量化选定的 300（天津普林 PE≈348~359）")
        os.environ["WOLF_LOWDIP_JUNK_MAX_PE"] = "150"
        self.assertEqual(G.max_pe(), 150.0)
        self.assertEqual(G.earn_flags(), ("bad",))
        os.environ["WOLF_LOWDIP_JUNK_EARN"] = "bad,flat"
        self.assertEqual(G.earn_flags(), ("bad", "flat"))

    def test_fail_open_without_tag(self):
        os.environ["WOLF_LOWDIP_JUNK_GATE"] = "1"
        os.environ.pop("WOLF_FUND_TAG", None)
        os.environ.pop("SZ_TAG", None)
        try:
            self.assertFalse(G.blocked("SZ002134", day="20260113")[0],
                             "没有数据源(tag) ⇒ 放行（fail-open，绝不因取数失败砍候选）")
        finally:
            self.setUp()


class TestRealData(unittest.TestCase):
    """对着真实缓存验：天津普林该被拦、博敏电子/工业富联不该被拦。"""

    def setUp(self):
        os.environ["WOLF_LOWDIP_JUNK_GATE"] = "1"
        os.environ["WOLF_FUND_TAG"] = "t2"
        sys.path.insert(0, os.path.join(ROOT, "jobs"))
        import bt_fund_asof as F
        F.ROOT = os.path.join(ROOT, "data", "_bt_fund")

    def tearDown(self):
        for k in ("WOLF_LOWDIP_JUNK_GATE", "WOLF_FUND_TAG"):
            os.environ.pop(k, None)

    def test_tianjin_putian_blocked(self):
        ok, why = G.blocked("SZ002134", day="20260113", tag="t2")
        self.assertTrue(ok, "天津普林 20260113：业绩 bad ∧ PE≈348 ⇒ 拦")
        self.assertIn("杂毛门", why)

    def test_others_pass(self):
        for s in ("SH603936", "SH601138"):     # 博敏电子（业绩 good）/ 工业富联
            ok, why = G.blocked(s, day="20260113", tag="t2")
            self.assertFalse(ok, "%s 不该被这道门拦（%s）" % (s, why))

    def test_index_aware(self):
        """as-of 正确性：同一票在不同日期结论可以不同（PE/业绩都是当日口径）。"""
        ok13, _ = G.blocked("SZ002134", day="20260113", tag="t2")
        ok30, _ = G.blocked("SZ002134", day="20260330", tag="t2")
        self.assertTrue(ok13)
        self.assertTrue(ok30, "20260330 它 PE 已 >1000 ⇒ 仍应拦")


if __name__ == "__main__":
    unittest.main()

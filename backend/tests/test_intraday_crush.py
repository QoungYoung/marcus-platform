# -*- coding: utf-8 -*-
"""intraday_crush 单测：盘中「急杀分」判据（C2′ 同日准入 + C2″ 极端门）。

依据（台账 I 节）：同日按急杀分取前 2 ⇒ 净差 +25,841（反方向按"企稳确认度"排是 −3,889）；
极端门（放量∧破前低∧破VWAP）拦 11/547 笔、拦对、跨臂 4✅/1❌。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import intraday_crush as C  # noqa: E402

MINS = os.path.join(ROOT, "data", "_bt_full", "mins")


class TestSwitch(unittest.TestCase):
    def setUp(self):
        for k in ("WOLF_CRUSH_GATE", "WOLF_CRUSH_EXTREME", "WOLF_CRUSH_ADMIT", "WOLF_CRUSH_ADMIT_K",
                  "WOLF_CRUSH_VOL_MIN", "WOLF_MINS_DIR"):
            os.environ.pop(k, None)

    def test_default_off(self):
        self.assertFalse(C.enabled(), "库内默认关 ⇒ 生产零影响")
        ok, why, sc = C.check("SH603936", "20260113", "09:40")
        self.assertTrue(ok, "关着时一律放行")

    def test_params(self):
        self.assertEqual(C.vol_min(), 1.5)
        self.assertEqual(C.admit_k(), 3)
        os.environ["WOLF_CRUSH_VOL_MIN"] = "2.0"
        os.environ["WOLF_CRUSH_ADMIT_K"] = "5"
        self.assertEqual(C.vol_min(), 2.0)
        self.assertEqual(C.admit_k(), 5)

    def test_fail_open_without_data(self):
        os.environ["WOLF_CRUSH_GATE"] = "1"
        os.environ["WOLF_MINS_DIR"] = "/nonexistent"
        try:
            ok, why, sc = C.check("SH603936", "20260113", "09:40")
            self.assertTrue(ok, "取不到分钟档 ⇒ 放行（fail-open）")
        finally:
            self.setUp()


class TestScoreDirection(unittest.TestCase):
    """急杀分方向：放量 + 跌破均价 + 靠日内低点 ⇒ 分高（对应语料『急杀可以买』）。"""

    def test_crush_beats_calm(self):
        crush = {"vr": 2.0, "below_vwap_pct": -2.0, "day_pos": 0.05}
        calm = {"vr": 0.7, "below_vwap_pct": 0.5, "day_pos": 0.9}
        self.assertGreater(C.crush_score(crush), C.crush_score(calm))
        self.assertGreater(C.crush_score(crush), 0)

    def test_missing_fields_are_neutral(self):
        self.assertEqual(C.crush_score({}), 0.0, "缺字段不产生分（也不崩）")


class TestExtremeGate(unittest.TestCase):
    def setUp(self):
        os.environ["WOLF_CRUSH_GATE"] = "1"
        os.environ["WOLF_CRUSH_EXTREME"] = "1"
        os.environ["WOLF_CRUSH_VOL_MIN"] = "1.5"

    def tearDown(self):
        for k in ("WOLF_CRUSH_GATE", "WOLF_CRUSH_EXTREME", "WOLF_CRUSH_VOL_MIN"):
            os.environ.pop(k, None)

    def test_all_three_required(self):
        base = {"vr": 2.0, "prev_low": 10.0, "above_prev_low": False,
                "vwap": 10.5, "above_vwap": False}
        self.assertTrue(C.extreme(dict(base))[0], "放量+破前低+破均价 ⇒ 拦")
        self.assertFalse(C.extreme({**base, "vr": 1.0})[0], "缩量 ⇒ 不拦")
        self.assertFalse(C.extreme({**base, "above_prev_low": True})[0], "未破前低 ⇒ 不拦")
        self.assertFalse(C.extreme({**base, "above_vwap": True})[0], "站上均价 ⇒ 不拦")

    def test_prev_low_required(self):
        f = {"vr": 2.0, "prev_low": None, "above_prev_low": True, "vwap": 10.5, "above_vwap": False}
        self.assertFalse(C.extreme(f)[0], "没有前低数据 ⇒ 不拦（fail-open）")


class TestAdmitSameDay(unittest.TestCase):
    def setUp(self):
        os.environ.update(WOLF_CRUSH_GATE="1", WOLF_CRUSH_EXTREME="0", WOLF_CRUSH_ADMIT="1",
                          WOLF_CRUSH_ADMIT_K="2")
        C.reset_day()

    def tearDown(self):
        for k in ("WOLF_CRUSH_GATE", "WOLF_CRUSH_EXTREME", "WOLF_CRUSH_ADMIT", "WOLF_CRUSH_ADMIT_K"):
            os.environ.pop(k, None)
        C.reset_day()

    def test_first_k_always_admitted(self):
        for sc in ({"vr": 0.7, "below_vwap_pct": 0.5, "day_pos": 0.9},   # 低分
                   {"vr": 0.7, "below_vwap_pct": 0.4, "day_pos": 0.8}):
            ok, s, _ = C.admit("drabt4", "20260113", sc)
            self.assertTrue(ok, "前 K 笔一律放行（不因分数低而拦）")

    def test_later_needs_above_median(self):
        C.admit("drabt4", "20260113", {"vr": 2.0, "below_vwap_pct": -3.0, "day_pos": 0.0})   # 高分
        C.admit("drabt4", "20260113", {"vr": 0.7, "below_vwap_pct": 0.5, "day_pos": 0.9})    # 低分
        ok_low, _, _ = C.admit("drabt4", "20260113", {"vr": 0.7, "below_vwap_pct": 0.5, "day_pos": 0.95})
        ok_high, _, _ = C.admit("drabt4", "20260113", {"vr": 2.5, "below_vwap_pct": -4.0, "day_pos": 0.0})
        self.assertFalse(ok_low, "超过 K 笔后，低分要被拦")
        self.assertTrue(ok_high, "高分仍放行")


class TestRealData(unittest.TestCase):
    def setUp(self):
        os.environ.update(WOLF_CRUSH_GATE="1", WOLF_CRUSH_EXTREME="1", WOLF_CRUSH_ADMIT="0",
                          WOLF_MINS_DIR=MINS)

    def tearDown(self):
        for k in ("WOLF_CRUSH_GATE", "WOLF_CRUSH_EXTREME", "WOLF_CRUSH_ADMIT", "WOLF_MINS_DIR"):
            os.environ.pop(k, None)

    def test_real_features(self):
        if not os.path.isdir(MINS):
            self.skipTest("无分钟档")
        f = C.feats("603936_SH", "20260113", "09:40")
        self.assertTrue(f, "能算出来")
        self.assertAlmostEqual(f["px"], 12.78, places=2)
        self.assertFalse(f["above_vwap"], "0113 09:40 在均价下方（放量下跌中）")
        g = C.feats("603936_SH", "20260115", "10:00")
        self.assertTrue(g and g["above_vwap"], "0115 真企稳：站上均价")

    def test_asof_no_future(self):
        """同一票同一日，09:40 与 14:55 的特征必须不同（只用 ≤T 数据）。"""
        a = C.feats("603936_SH", "20260113", "09:40")
        b = C.feats("603936_SH", "20260113", "14:55")
        self.assertNotAlmostEqual(a["px"], b["px"], places=2)


if __name__ == "__main__":
    unittest.main()


    def test_l416_no_midcrash_switch(self):
        """账本 §9.416：不许在「杀中」买（开关默认关 ✓）"""
        import os as _o
        _p = os.path.join(_o.path.dirname(os.path.dirname(_o.path.dirname(
            _o.path.abspath(__file__)))), "apps", "main_line", "intraday_crush.py")
        _src = open(_p, encoding="utf-8").read()
        self.assertIn("WOLF_CRUSH_NO_MIDCRASH", _src)
        self.assertIn("WOLF_CRUSH_DEEP_ONLY", _src)
        self.assertIn('os.getenv("WOLF_CRUSH_NO_MIDCRASH", "0")', _src, "开关必须默认关")

# -*- coding: utf-8 -*-
"""P3 三仓档位模型 · 纯规则单测（不依赖 DB / 行情）。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services import position_tier as pt


def _cfg():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "p3_position_tiers.json")
    return json.load(open(p, encoding="utf-8"))


class ThreeTierGateTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg()

    def _call(self, op, intent, has_base=False, t_universe=False, rel_low=False, mainline=False):
        return pt.three_tier_gate(wave_state={"operation": op, "sub_level": "3-3", "level": "d3"},
                                  intent=intent, has_base=has_base, t_universe=t_universe,
                                  rel_low=rel_low, mainline_dir=mainline, cfg=self.cfg)

    def test_build_allows_new_and_refill(self):
        d = self._call("build", "new_base")
        self.assertTrue(d["intent_allowed"]); self.assertEqual(d["cap_pct"], 10.0)
        self.assertEqual(d["base_mode"], "new"); self.assertEqual(d["cash_floor_pct"], 25.0)
        d2 = self._call("build", "refill_base", t_universe=True)
        self.assertTrue(d2["intent_allowed"]); self.assertEqual(d2["cap_pct"], 8.0)

    def test_t_only_forbids_new_but_allows_t_refill_and_base_refill(self):
        d = self._call("t_only", "new_base")
        self.assertFalse(d["intent_allowed"]); self.assertEqual(d["tier"], "REJECT")
        d2 = self._call("t_only", "refill_base", t_universe=True)
        self.assertTrue(d2["intent_allowed"]); self.assertEqual(d2["cap_pct"], 8.0)
        d3 = self._call("t_only", "t_refill", has_base=True)
        self.assertTrue(d3["intent_allowed"]); self.assertEqual(d3["cap_pct"], 5.0)
        # 无底仓且不在 T 宇宙 → t_refill/refill 都不能
        d4 = self._call("t_only", "t_refill")
        self.assertFalse(d4["intent_allowed"])

    def test_t_only_refill_without_t_universe_rejected(self):
        d = self._call("t_only", "refill_base")
        self.assertFalse(d["intent_allowed"])

    def test_side_ambush_needs_rel_low(self):
        d1 = self._call("side", "new_base")
        self.assertFalse(d1["intent_allowed"])
        d2 = self._call("side", "new_base", rel_low=True)
        self.assertTrue(d2["intent_allowed"]); self.assertEqual(d2["cap_pct"], 3.0)
        self.assertEqual(d2["base_mode"], "ambush")

    def test_defense_no_new_but_add_allowed_for_mainline_base(self):
        d1 = self._call("defense", "new_base")
        self.assertFalse(d1["intent_allowed"])
        d2 = self._call("defense", "add_base", has_base=True, mainline=True)
        self.assertTrue(d2["intent_allowed"]); self.assertEqual(d2["cap_pct"], 3.0)
        d3 = self._call("defense", "add_base", has_base=True, mainline=False, rel_low=False)
        self.assertFalse(d3["intent_allowed"])
        self.assertEqual(d1["cash_floor_pct"], 45.0)

    def test_exit_only_t_on_existing_base(self):
        for intent in ("new_base", "add_base", "refill_base"):
            d = self._call("exit", intent, has_base=True, t_universe=True, mainline=True)
            self.assertFalse(d["intent_allowed"], intent)
        d = self._call("exit", "t_refill", has_base=True)
        self.assertTrue(d["intent_allowed"]); self.assertEqual(d["cap_pct"], 5.0)
        self.assertEqual(d["cash_floor_pct"], 50.0)

    def test_unknown_op_falls_back_side_conservative(self):
        d = pt.three_tier_gate(wave_state={"operation": "unknown"}, intent="new_base", cfg=self.cfg)
        self.assertFalse(d["intent_allowed"])
        self.assertEqual(d["wave"]["operation"], "side")

    def test_summarize_nonempty(self):
        d = self._call("build", "new_base")
        s = pt.summarize(d)
        self.assertTrue(s.startswith("[P3三仓]"))
        self.assertIn("✅允许", s)


if __name__ == "__main__":
    unittest.main()

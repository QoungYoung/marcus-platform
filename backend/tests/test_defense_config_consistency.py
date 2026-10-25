# -*- coding: utf-8 -*-
"""防御口径一致性测试（2026-10-25 拍板）。

锁定四件事，防止再出现 exit=70/50/30/0 四个版本：
  1. G3 = 0%（空仓），cash floor 100；G0 不设总仓上限；
  2. G2 = 30%，add_base 默认禁止；
  3. 周末避险 65%（缩量+不拉升），50% 只作回退；
  4. 旧 Pi stance 表 / risk_manager 不再作为仓位权威。

不依赖 DB：`WOLF_DISCIPLINE_CFG_DB=0` 走文件/默认。
"""
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("WOLF_DISCIPLINE_CFG_DB", "0")
os.environ.setdefault("P3_USE_TIER_TARGETS", "1")

REPO = Path(__file__).resolve().parents[2]


def _load(rel):
    with open(REPO / rel, encoding="utf-8") as f:
        return json.load(f)


class DefenseConfigTest(unittest.TestCase):
    def test_wolf_discipline_tier_targets(self):
        d = _load("config/wolf_discipline.json")
        t = d["position_cap"]["tier_targets"]
        self.assertEqual(t["t_only"], 50)
        self.assertEqual(t["side"], 50)
        self.assertEqual(t["defense"], 30)
        self.assertEqual(t["exit"], 0, "G3 必须是 0%（下跌趋势不做）")

    def test_p3_cash_floors_and_defense_add(self):
        d = _load("config/p3_position_tiers.json")
        self.assertEqual(d["wave"]["defense"]["cash_floor_pct"], 70)
        self.assertEqual(d["wave"]["exit"]["cash_floor_pct"], 100)
        self.assertFalse(d["wave"]["defense"]["intents"]["add_base"]["allow"],
                         "G2 默认禁止 add_base；人工例外不走 P3")

    def test_defense_cash_ssot(self):
        d = _load("config/defense_cash.json")
        self.assertEqual(d["levels"]["G3"]["target_pct"], 0)
        self.assertEqual(d["levels"]["G3"]["buckets"], {"base": 0, "t": 0, "cash": 100})
        self.assertEqual(d["levels"]["G2"]["target_pct"], 30)
        self.assertFalse(d["levels"]["G2"]["allow_add"])
        self.assertEqual(d["weekend_hedge"]["target_pct"], 65)
        self.assertEqual(d["weekend_hedge"]["fallback_target_pct"], 50)


class PositionTierTest(unittest.TestCase):
    def test_defense_add_base_rejected(self):
        from app.services.position_tier import three_tier_gate
        d = three_tier_gate(
            ts_code="600000.SH", intent="add_base",
            wave_state={"level": "d4", "sub_level": "4-5", "operation": "defense"},
            has_base=True, mainline_dir=True,
        )
        self.assertFalse(d["intent_allowed"], d)
        self.assertEqual(d["cap_pct"], 0.0)

    def test_exit_cash_floor_100(self):
        from app.services.position_tier import three_tier_gate
        d = three_tier_gate(
            ts_code="600000.SH", intent="t_refill",
            wave_state={"level": "down", "sub_level": "下跌1浪", "operation": "exit"},
            has_base=True,
        )
        self.assertEqual(d["cash_floor_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()

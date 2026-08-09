# -*- coding: utf-8 -*-
"""黄金坑配置测试：防御轮动池改为 515080 中证红利 + 永久持有模式。"""
import unittest

from app.services.golden_pit_config import (
    DEFENSE_INDICES,
    DEFENSE_TAKEOVER_WEIGHTS,
    _describe_entry_strategy,
    _describe_exit_strategy,
)


class TestDefenseRotationConfig(unittest.TestCase):
    def test_defense_pool_uses_csi_dividend_515080(self):
        self.assertNotIn("510880", DEFENSE_TAKEOVER_WEIGHTS)
        self.assertIn("515080", DEFENSE_TAKEOVER_WEIGHTS)
        self.assertEqual(DEFENSE_TAKEOVER_WEIGHTS["515080"], 0.20)

    def test_five_assets_equal_weight(self):
        self.assertEqual(len(DEFENSE_TAKEOVER_WEIGHTS), 5)
        self.assertEqual(set(DEFENSE_TAKEOVER_WEIGHTS.values()), {0.2})

    def test_515080_config(self):
        cfg = DEFENSE_INDICES["515080"]
        self.assertEqual(cfg["name"], "中证红利")
        self.assertEqual(cfg["etf_code"], "SH515080")
        self.assertEqual(cfg["tier"], "defense_rotation")
        self.assertIsNone(cfg.get("arkvol_code"))

    def test_defense_strategy_text_is_hold_until_reentry(self):
        for cfg in DEFENSE_INDICES.values():
            self.assertIn("随宽基撤场等权轮入", _describe_entry_strategy(cfg))
            self.assertIn("持有至宽基重新入场", _describe_exit_strategy(cfg))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""黄金坑报告模块单测：build_v2_summary 行业监测块（回归 NameError: status/lines）。"""
import unittest
from unittest import mock

from app.services.golden_pit_report import build_v2_summary


def _dummy_confirmation():
    return {k: {"confirmed": False} for k in ("layer1", "layer2", "layer3")}


class TestBuildV2Summary(unittest.TestCase):
    @mock.patch("app.services.golden_pit_industry_service.get_industry_config", return_value={"enabled": False})
    def test_no_crash_when_industry_disabled(self, mock_cfg):
        """回归: 之前 industry_monitor=None 时 build_v2_summary 直接 NameError('status')。"""
        s = build_v2_summary([], {}, _dummy_confirmation(), {}, industry_monitor=None)
        self.assertIsInstance(s, str)
        mock_cfg.assert_called_once()

    def test_industry_block_when_enabled(self):
        monitor = {
            "as_of": "2026-08-13",
            "enabled": True,
            "industries": [
                {"id": "semicon", "name": "半导体", "greed_pct": 0.03, "drawdown": -0.645,
                 "in_pit": True, "window_day": 2, "planned_amount": 1314.29,
                 "actual_amount": 1314.29, "total_invested": 2628.58},
            ],
            "cash_pool": {"total_nav": 54761.9, "cash": 54761.9, "available_cash": 43809.52,
                          "planned_total": 1314.29, "actual_total": 1314.29, "cut_items": []},
            "notes": [],
        }
        s = build_v2_summary([], {}, _dummy_confirmation(), {}, industry_monitor=monitor)
        self.assertIn("全行业监测", s)
        self.assertIn("半导体", s)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""marcus_trade QQ 通知带账户标识 单元测试。"""
import unittest
from unittest import mock

from app.core.trading.marcus_trade import MarcusVNPyExecutor, account_label


class TestNotifyAccountLabel(unittest.TestCase):

    def test_account_label_mapping(self):
        self.assertIn("做T账户", account_label("t"))
        self.assertIn("股票账户", account_label("stock"))
        self.assertIn("黄金坑", account_label("golden_pit"))
        self.assertEqual(account_label("other"), "other（other）")

    @mock.patch("app.services.qqbot_service.send_qq_notification")
    def test_notify_buy_includes_account(self, send):
        ex = MarcusVNPyExecutor(account_id="t")
        ex._notify_buy("SZ002965", 34.29, 3000, "V反短线建仓（vrebounce）", 102870.0)
        msg = send.call_args[0][0]
        self.assertIn("账户: 做T账户（t）", msg)
        self.assertIn("SZ002965", msg)

    @mock.patch("app.services.qqbot_service.send_qq_notification")
    def test_notify_sell_includes_account(self, send):
        ex = MarcusVNPyExecutor(account_id="t")
        ex._notify_sell("SZ002965", 37.0, 3000, "vrebounce 止盈 +8% 清仓", 8000.0, avg_cost=34.29)
        msg = send.call_args[0][0]
        self.assertIn("账户: 做T账户（t）", msg)
        self.assertIn("止盈", msg)


if __name__ == "__main__":
    unittest.main()

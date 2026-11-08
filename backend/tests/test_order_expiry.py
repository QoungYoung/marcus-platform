# -*- coding: utf-8 -*-
"""挂单日终作废 / 冻结资金释放（2026-09-18 用户："建仓/冻结资金释放修复下"）单测。

实测依据：回测两臂各有一笔未成交买单把资金冻结到窗口结束——
  drab_on  SZ002602 买 1400@19.081（01-26 挂出）→ 26,726.76 元冻结至 02-03；
  drab_off SH603203 买 700@36.723（01-08 挂出）→ 25,718.95 元冻结至 02-03；
  另有卖单 SH603203 卖 100@38.781 卡在"提交中"⇒ 持仓 frozen 不释放。
"""
import os
import unittest

from app.services import t_order_expiry as X

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AmountTest(unittest.TestCase):
    def test_release_amount_uses_untraded_part(self):
        self.assertAlmostEqual(X.release_amount(19.081, 1400, 0), 26713.4, places=2)
        self.assertAlmostEqual(X.release_amount(36.723, 700, 0), 25706.1, places=2)
        self.assertAlmostEqual(X.release_amount(10.0, 1000, 400), 6000.0, places=2)   # 部分成交只释放剩余

    def test_release_amount_never_negative(self):
        self.assertEqual(X.release_amount(10.0, 100, 100), 0.0)      # 已全部成交 → 不释放
        self.assertEqual(X.release_amount(0, 100, 0), 0.0)
        self.assertEqual(X.release_amount(None, None, None), 0.0)

    def test_remaining_volume(self):
        self.assertEqual(X.remaining_volume(100, 30), 70)
        self.assertEqual(X.remaining_volume(100, 100), 0)
        self.assertEqual(X.remaining_volume(None, None), 0)


class SwitchTest(unittest.TestCase):
    def test_library_default_off(self):
        src = open(os.path.join(ROOT, "app", "services", "t_order_expiry.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_EXPIRE_STALE_ORDERS", "0")', src)

    def test_disabled_returns_skip_without_touching_db(self):
        self.assertEqual(X.expire_stale_orders(conn=None, account_id="x", today="20260203",
                                              enabled_override=False), {"skipped": "switch_off"})

    def test_backtest_driver_enables(self):
        src = open(os.path.join(ROOT, "..", "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        self.assertIn('("WOLF_EXPIRE_STALE_ORDERS", "1")', src)
        self.assertIn("expire_stale_orders(conn=_xconn", src)          # 日启动时真的调用

    def test_gateway_hook_exists_and_is_gated(self):
        src = open(os.path.join(ROOT, "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("expire_once_per_day(account_id)", src)
        self.assertIn("if _oex.enabled():", src)

    def test_clears_fee_residual_when_no_pending_left(self):
        """释放额按 price×剩余量、冻结含手续费 ⇒ 会残留几元；无未终结挂单时应清零。"""
        src = open(os.path.join(ROOT, "app", "services", "t_order_expiry.py"), encoding="utf-8").read()
        self.assertIn("frozen_cash = 0, updated_at = now()", src)
        self.assertIn("cleared_residual", src)

    def test_sql_targets_only_stale_pending_orders(self):
        """只动"上一交易日及更早"的 提交中/部分成交，绝不碰当日挂单与已成订单。"""
        src = open(os.path.join(ROOT, "app", "services", "t_order_expiry.py"), encoding="utf-8").read()
        self.assertIn("substr(created_at, 1, 10) < %s", src)
        self.assertIn("status IN ('提交中','部分成交')", src)
        self.assertIn("frozen_cash = GREATEST(COALESCE(frozen_cash,0) - %s, 0)", src)
        self.assertIn("available_cash = COALESCE(available_cash,0) + %s", src)


if __name__ == "__main__":
    unittest.main()

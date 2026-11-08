# -*- coding: utf-8 -*-
"""成交价必须尊重腿的设计价（WOLF_T_LEG_PRICE_FILL，默认关）。

背景（用户 2026-09-18 实测）：2026-01-09 SZ002429 **卖 7.792 / 买 7.792 同价成交**——
成交价一律取"触发时现价 ×(1∓0.1%)"，腿自己的 target_price/sell_target_price/reinform_price
不参与定价 ⇒ 震荡日里一来一回毛价差 0，扣往返成本 0.13% 必亏。
语料：做T 目标 3-4 点、ETF 2 点、"千万不要 1 个点的波动用糖葫芦战法"；代码自有 `reinform_price = 高抛×0.996`。
开关关 ⇒ 旧行为逐字一致；开 ⇒ 买腿以设计买价为硬上限（高于则**不回补**）、卖腿以兑现价为下限。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_monitor as TM  # noqa: E402


class LegPriceGateTest(unittest.TestCase):
    def test_default_off_is_legacy(self):
        cond = {"direction": "buy", "reinform_price": 7.70, "target_price": 7.75}
        r = TM.leg_price_gate(cond, 7.80, 0.001, enabled=False)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["bid"], round(7.80 * 0.999, 3))

    def test_buy_blocked_when_above_design_price(self):
        """现价高于回补价 ⇒ 不回补（堵掉同价来回）。"""
        cond = {"direction": "buy", "reinform_price": 7.76, "target_price": 7.70}
        r = TM.leg_price_gate(cond, 7.80, 0.001, enabled=True)
        self.assertFalse(r["ok"], r)

    def test_buy_allowed_at_or_below_design_price(self):
        cond = {"direction": "buy", "reinform_price": 7.83, "target_price": 7.70}
        r = TM.leg_price_gate(cond, 7.80, 0.001, enabled=True)
        self.assertTrue(r["ok"])
        self.assertLessEqual(r["bid"], 7.83)

    def test_buy_uses_reinform_over_target(self):
        cond = {"direction": "buy", "reinform_price": 7.70, "target_price": 7.90}
        r = TM.leg_price_gate(cond, 7.70, 0.001, enabled=True)   # 现价=回补价，未高于上限 → 放行
        self.assertTrue(r["ok"])
        self.assertLessEqual(r["bid"], 7.70)

    def test_sell_floored_at_design_price(self):
        cond = {"direction": "sell", "sell_target_price": 8.10}
        r = TM.leg_price_gate(cond, 8.00, 0.001, enabled=True)
        self.assertGreaterEqual(r["ask"], 8.10)

    def test_switch_constant_default_off(self):
        self.assertFalse(TM.LEG_PRICE_FILL, "必须默认关（口径变更）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

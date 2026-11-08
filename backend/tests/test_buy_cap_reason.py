# -*- coding: utf-8 -*-
"""买腿上限**归因文案**回归测试（2026-09-17 用户要求「改清楚」）。

旧文案 `当前档位 L2 禁止低吸` 把两件不相干的事混在一起：
  · `tier` 是**行情档位**（`_floor_tier`：HALT/近跌停→L0、CAUTIOUS→L1、其余→L2）；
  · 真正拦下来的多是**无底仓**（可卖=0）且 `condition_id=None`（`wolf_*` 触发都是这种）。
新文案以 `买腿上限0｜` 开头点名真实原因，便于日志/看板按前缀分类与人工排查。
口径与旧实现完全一致（仅文案/归因变化），故同时断言上限数值不回退。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_gateway as G            # noqa: E402

HELD = {"SZ002587": {"symbol": "SZ002587", "volume": 4600, "sellable": 4600}}
FLAT = {}


class BuyCapReasonTest(unittest.TestCase):
    def _why(self, tier, ledger, condition_id=None, price=6.9):
        with mock.patch.object(G, "get_sellable_ledger", lambda acc=None: ledger):
            cap, why = G._max_buy_volume_ex("SZ002587", tier, ledger, price=price,
                                            condition_id=condition_id)
        return cap, why

    def test_l0_is_market_regime_not_position(self):
        cap, why = self._why(G.TIER_L0, HELD)
        self.assertEqual(cap, 0)
        self.assertTrue(why.startswith("买腿上限0｜"))
        self.assertIn("行情档位 L0", why)

    def test_no_base_without_condition_says_so(self):
        cap, why = self._why(G.TIER_L2, FLAT, condition_id=None)
        self.assertEqual(cap, 0)
        self.assertIn("无底仓", why)
        self.assertIn("condition_id 为空", why)
        self.assertIn("与本次拦截无关", why, "必须点明行情档位不是原因（旧文案的误导点）")
        self.assertNotIn("禁止低吸", why)

    def test_no_base_condition_path_passes(self):
        sz = {"pass": True, "suggest_volume": 3600}
        with mock.patch("app.services.t_build.build_sizing", lambda s, p: sz):
            cap, why = self._why(G.TIER_L2, FLAT, condition_id=123)
        self.assertEqual(cap, 3600)
        self.assertIn("条件单建仓规模放行", why)

    def test_no_base_condition_path_rejected(self):
        sz = {"pass": False, "reason": "单标仓位上限"}
        with mock.patch("app.services.t_build.build_sizing", lambda s, p: sz):
            cap, why = self._why(G.TIER_L2, FLAT, condition_id=123)
        self.assertEqual(cap, 0)
        self.assertIn("建仓规模未通过", why)
        self.assertIn("单标仓位上限", why)

    def test_no_base_condition_path_error(self):
        def _boom(s, p):
            raise RuntimeError("sizing down")
        with mock.patch("app.services.t_build.build_sizing", _boom):
            cap, why = self._why(G.TIER_L2, FLAT, condition_id=123)
        self.assertEqual(cap, 0)
        self.assertIn("计算失败", why)

    # ── 上限数值不回退 ──
    def test_caps_unchanged_with_base(self):
        self.assertEqual(self._why(G.TIER_L2, HELD)[0], 4600)
        self.assertEqual(self._why(G.TIER_L1, HELD)[0], 2300)
        self.assertEqual(self._why(G.TIER_L3, HELD)[0], 6900)

    def test_wrapper_compat_returns_int(self):
        with mock.patch.object(G, "get_sellable_ledger", lambda acc=None: HELD):
            v = G._max_buy_volume("SZ002587", G.TIER_L2, HELD)
        self.assertIsInstance(v, int)
        self.assertEqual(v, 4600)

    def test_old_misleading_text_not_used_runtime(self):
        src = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
        self.assertNotIn('f"当前档位 {tier} 禁止低吸"', src, "旧误导文案不得再作为运行时 reason")
        self.assertIn("买腿上限0｜", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

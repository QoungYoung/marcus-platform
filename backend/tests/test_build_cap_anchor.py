# -*- coding: utf-8 -*-
"""新建仓买量锚（`WOLF_BUILD_CAP_ANCHOR`）回归测试 —— 2026-09-22 用户拍板"先做 3"。

## 病灶（实测 T6 0401→0428 整整 3 周空转）

买腿上限锚在「**可卖底仓** × 档位系数」上（`t_gateway._max_buy_volume_ex`）⇒
  · 空仓（可卖 0）⇒ 上限 0，且非条件单的文案是"账上无仓可回补"；
  · 一手仓 ⇒ 上限 100 股 ⇒ 200 股的建仓腿直接被拦
    （原文「买腿 200 超过档位 L2 上限 100（买腿≤可卖底仓）」，它拦掉过后来 +20% 的 SZ002290）。
⇒ **空仓状态无法自愈**：唯一能重开仓的新建仓腿被它自己的"可卖底仓"锁死。
4 月被拦的买腿按 (票,日) 去重后 83 个独立决策、事后 T+3 平均 +3.56%（代理口径）。

## 本开关做什么

开关开 ⇒ **只对"账上确无该标的"**（`sellable<=0 ∧ volume<=0`）的买腿，把锚换成**资金**：
复用两分法 `t_capacity.clamp_buy_volume(is_build=True)`（底仓 65% 缺口 / `WOLF_BUILD_STEP_FRACTION`
+ 最小有效规模 `MIN_BUILD_NOTIONAL`），再用**可用现金**兜一道。
有底仓的加仓/做T/回补腿**一律走旧口径**（那里"买腿≤可卖底仓"是有意为之的防堆仓）。

## 本测试守什么

1. **默认关 ⇒ 逐字旧行为**（生产零影响）；
2. 开 ⇒ 空仓能拿到资金锚上限，且受可用现金约束；
3. 开 ⇒ **有底仓的标的不受任何影响**（数值与文案都不变）；
4. 开 ⇒ 当日买入（volume>0 但 sellable=0）**不算新建仓**（防同日堆仓）；
5. L0（HALT/近跌停）仍然优先拦死；
6. 取不到总资产 / 抛异常 ⇒ **回退旧口径**（fail-safe，不放大）。
"""
import os
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

FLAT = {}                                          # 空仓
HELD = {"SZ002587": {"symbol": "SZ002587", "volume": 4600, "sellable": 4600}}
JUST_BOUGHT = {"SZ002587": {"symbol": "SZ002587", "volume": 1000, "sellable": 0}}   # T+1 锁定


class BuildCapAnchorTest(unittest.TestCase):
    def setUp(self):
        # 建仓步长固定成 3，测试数值才可复现（回测 pins 可能设 2）
        self._env = mock.patch.dict(os.environ, {"WOLF_BUILD_STEP_FRACTION": "3"}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _call(self, tier=G.TIER_L2, ledger=FLAT, condition_id=None, price=40.0,
              anchor=False, equity=250000.0, cash=250000.0, eq_raises=False,
              kind="trend_break_buy"):
        def _eq(acc, ledger=None, **kw):
            if eq_raises:
                raise RuntimeError("db down")
            return equity
        with mock.patch.object(G, "_BUILD_CAP_ANCHOR", anchor), \
             mock.patch.object(G, "_account_available_cash", lambda acc: cash), \
             mock.patch("app.services.t_capacity.account_equity", _eq), \
             mock.patch.object(G, "get_sellable_ledger", lambda acc=None: ledger):
            return G._max_buy_volume_ex("SZ002587", tier, ledger, price=price,
                                        condition_id=condition_id, kind=kind)

    # ── 1. 默认关：逐字旧行为 ────────────────────────────────────────────
    def test_default_off_keeps_old_behavior(self):
        cap, why = self._call(anchor=False)
        self.assertEqual(cap, 0)
        self.assertIn("无底仓", why)
        self.assertIn("condition_id 为空", why)

    def test_source_default_is_off(self):
        src = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("WOLF_BUILD_CAP_ANCHOR", "0")', src, "库内默认必须为 0（生产零影响）")

    # ── 2. 开：空仓拿到资金锚 ────────────────────────────────────────────
    def test_on_releases_flat_account(self):
        cap, why = self._call(anchor=True)
        # base_gap = 250000×0.65 = 162500；/3 = 54166.7；/40 = 1354 → 整手 1300
        self.assertEqual(cap, 1300)
        self.assertIn("新建仓资金锚", why)
        self.assertIn("WOLF_BUILD_CAP_ANCHOR=1", why)

    def test_on_respects_build_step_two(self):
        with mock.patch.dict(os.environ, {"WOLF_BUILD_STEP_FRACTION": "2"}, clear=False):
            cap, _ = self._call(anchor=True)
        self.assertEqual(cap, 2000, "步长 2 ⇒ 缺口/2 = 81250 → /40 = 2031 → 整手 2000")

    def test_on_clamped_by_cash(self):
        cap, why = self._call(anchor=True, cash=5000.0)
        self.assertEqual(cap, 100, "现金 5000×0.98/40 = 122 → 整手 100")
        self.assertIn("可用资金", why)

    def test_on_zero_gap_falls_back_to_old(self):
        # 别的票已占满 65% 目标（缺口 0）⇒ 资金锚=0，不高于旧上限 ⇒ **落回旧口径**（只抬不压）
        heavy = {"OTHER": {"symbol": "OTHER", "volume": 100000, "sellable": 100000,
                           "avg_price": 2.0}}          # 持仓市值 200000 > 100000×0.65
        cap, why = self._call(ledger=heavy, anchor=True, equity=100000.0, cash=100000.0)
        self.assertEqual(cap, 0)
        self.assertNotIn("新建仓资金锚", why)
        self.assertIn("无底仓", why)

    def test_on_partial_gap_uses_gap(self):
        cap, _ = self._call(anchor=True, equity=100000.0, cash=80000.0)
        # 缺口 = 65000；/3 = 21666.7；/40 = 541 → 整手 500；现金兜底 80000×0.98/40 = 1960 手数 1900 ⇒ 取 500
        self.assertEqual(cap, 500)

    def test_on_no_cash_blocks(self):
        cap, why = self._call(anchor=True, cash=0.0)
        self.assertEqual(cap, 0, "可用资金 0 ⇒ 不该放行")
        self.assertIn("买腿上限0｜", why)

    def test_on_cash_read_failure_does_not_block(self):
        # 现金读失败（None）= 未知 ⇒ 不加这道约束（fail-open），仍用资金锚
        cap, why = self._call(anchor=True, cash=None)
        self.assertEqual(cap, 1300)
        self.assertNotIn("按可用资金", why, "读失败 ⇒ 不出现现金收敛那句（前缀自带的说明不算）")

    # ── 3. 开：有底仓的标的完全不受影响 ──────────────────────────────────
    def test_on_does_not_touch_held_symbol(self):
        for tier, expect in ((G.TIER_L2, 4600), (G.TIER_L1, 2300), (G.TIER_L3, 6900)):
            cap, why = self._call(tier=tier, ledger=HELD, anchor=True)
            self.assertEqual(cap, expect, "持仓票必须走旧口径（买腿≤可卖底仓×档位）")
            self.assertNotIn("新建仓资金锚", why)

    # ── 4. 做T回补腿型不适用（守狼大「没抄底没资格T」+ 防堆仓） ─────────────
    def test_on_take_kind_not_released(self):
        for k in ("wolf_zheng_t_buy", "custom_prevlow", "high_sell", "", None):
            cap, why = self._call(anchor=True, kind=k)
            self.assertEqual(cap, 0, "做T类/未知腿型必须走旧口径（%r）" % (k,))
            self.assertNotIn("新建仓资金锚", why)

    def test_on_same_day_buy_is_not_treated_as_new_position(self):
        # 建仓腿 + 当日买入（volume>0, sellable=0）：资金锚只抬不压 ⇒ 仍按资金锚抬到 1300
        cap, why = self._call(ledger=JUST_BOUGHT, anchor=True)
        self.assertIn("新建仓资金锚", why)
        self.assertEqual(cap, 1300)

    # ── 5. 只抬不压：资金锚小于旧上限时，原样落回旧口径 ──────────────────
    def test_on_never_tightens_below_old_cap(self):
        # 持仓 4600（旧上限 L2 = 4600），但缺口为 0（别的票已占满 65%）⇒ 资金锚 0 ⇒ 必须仍是 4600
        heavy = dict(HELD)
        heavy["OTHER"] = {"symbol": "OTHER", "volume": 100000, "sellable": 100000, "avg_price": 2.0}
        cap, why = self._call(ledger=heavy, anchor=True, equity=100000.0, cash=100000.0)
        self.assertEqual(cap, 4600, "资金锚不得把旧上限压低")

    # ── 6. 一手仓 + 建仓腿：旧上限 100，资金锚抬到 1300 ──────────────────
    def test_on_small_position_build_leg_raised(self):
        one = {"SZ002587": {"symbol": "SZ002587", "volume": 100, "sellable": 100}}
        cap, why = self._call(ledger=one, anchor=True)
        self.assertEqual(cap, 1300, "一手仓的建仓腿应抬到资金锚，而不是卡在 100 股")
        self.assertIn("可卖底仓×档位=100", why)

    def test_on_small_position_take_leg_unchanged(self):
        one = {"SZ002587": {"symbol": "SZ002587", "volume": 100, "sellable": 100}}
        cap, why = self._call(ledger=one, anchor=True, kind="wolf_zheng_t_buy")
        self.assertEqual(cap, 100, "做T回补腿仍受「买腿≤可卖底仓」约束")

    # ── 7. L0 优先 ──────────────────────────────────────────────────────
    def test_on_l0_still_blocked(self):
        cap, why = self._call(tier=G.TIER_L0, anchor=True)
        self.assertEqual(cap, 0)
        self.assertIn("行情档位 L0", why)

    # ── 6. fail-safe ────────────────────────────────────────────────────
    def test_on_equity_none_falls_back(self):
        cap, why = self._call(anchor=True, equity=None)
        self.assertEqual(cap, 0)
        self.assertIn("无底仓", why, "取不到总资产 ⇒ 回退旧口径（不放大）")

    def test_on_exception_falls_back(self):
        cap, why = self._call(anchor=True, eq_raises=True)
        self.assertEqual(cap, 0)
        self.assertIn("无底仓", why)

    def test_on_no_price_falls_back(self):
        cap, why = self._call(anchor=True, price=0)
        self.assertEqual(cap, 0)
        self.assertIn("无底仓", why)

    # ── 7. 与既有口径的兼容 ─────────────────────────────────────────────
    def test_wrapper_still_returns_int(self):
        with mock.patch.object(G, "_BUILD_CAP_ANCHOR", True), \
             mock.patch.object(G, "_account_available_cash", lambda acc: 250000.0), \
             mock.patch("app.services.t_capacity.account_equity", lambda acc, ledger=None, **kw: 250000.0):
            v = G._max_buy_volume("SZ002587", G.TIER_L2, FLAT, price=40.0, kind="trend_break_buy")
        self.assertIsInstance(v, int)
        self.assertEqual(v, 1300)


if __name__ == "__main__":
    unittest.main(verbosity=2)

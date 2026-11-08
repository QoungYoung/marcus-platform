# -*- coding: utf-8 -*-
"""持续腿重复写行的冷却（WOLF_PERSIST_LEG_COOLDOWN，默认关）单测。

背景：20260202 单日 7,260 行触发中 92% 来自 `custom_vwap_sell`/`custom_support_sell`/`high_sell`
这类**非消费式持续腿**（现价在线下方就每根 bar 重写一行），其中 94% 被网关拦；
行数≈条件数×48bar ⇒ 与 `_round`=675.4s（stop_loss 492.4s）同源。
修法：把既有 5 分钟同条件冷却从 `wolf_*` 扩到这几类；**默认关**（可能改变成交，属口径变更）。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


class PersistKindsTest(unittest.TestCase):
    def test_switch_defaults_off(self):
        import app.services.t_monitor as TM
        self.assertFalse(TM.PERSIST_LEG_COOLDOWN, "必须默认关（生产行为不变）")

    def test_kinds_are_the_measured_ones(self):
        import app.services.t_monitor as TM
        self.assertEqual(set(TM.PERSIST_KINDS),
                         {"custom_vwap_sell", "custom_support_sell", "high_sell"})

    def test_window_env(self):
        import os
        import importlib
        import app.services.t_monitor as TM
        with mock.patch.dict(os.environ, {"WOLF_PERSIST_COOLDOWN_SEC": "1800"}, clear=False):
            m = importlib.reload(TM)
            self.assertEqual(m.PERSIST_COOLDOWN_SEC, 1800.0)
        with mock.patch.dict(os.environ, {"WOLF_PERSIST_LEG_COOLDOWN": "1"}, clear=False):
            m2 = importlib.reload(TM)
            self.assertTrue(m2.PERSIST_LEG_COOLDOWN)
        importlib.reload(TM)          # 还原（默认关）

    def test_window_exceeds_bar_interval(self):
        """窗口必须 > bar 间距(300s)，否则判据恒不成立（2026-09-18 实测静默失效）。"""
        import app.services.t_monitor as TM
        self.assertGreater(TM.PERSIST_COOLDOWN_SEC, 300.0)

    def test_counter_exposed(self):
        import app.services.t_monitor as TM
        rt = TM.round_timing()
        self.assertIn("persist_cooldown_skip", rt)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class KindThrottleTest(unittest.TestCase):
    """第二步：按 (标的, 腿型) 的 bar 节流（不受条件行重建影响）。"""

    def setUp(self):
        import app.services.t_monitor as TM
        self.TM = TM
        TM.persist_throttle_reset()

    def _c(self, sym="SZ002429", kind="custom_vwap_sell"):
        return {"symbol": sym, "trigger_kind": kind}

    def test_off_by_default(self):
        self.assertFalse(self.TM.persist_kind_throttled(self._c(), enabled=False))
        self.assertFalse(self.TM.persist_kind_throttled(self._c(), enabled=False))

    def test_same_symbol_kind_within_window(self):
        with mock.patch.object(self.TM, "_bar_key", side_effect=["202601091000", "202601091005", "202601091010"]):
            self.assertFalse(self.TM.persist_kind_throttled(self._c(), enabled=True))   # 首行
            self.assertTrue(self.TM.persist_kind_throttled(self._c(), enabled=True))    # +5min → 拦
            self.assertTrue(self.TM.persist_kind_throttled(self._c(), enabled=True))    # +10min → 拦

    def test_allowed_after_window(self):
        with mock.patch.object(self.TM, "_bar_key", side_effect=["202601091000", "202601091020"]):
            self.assertFalse(self.TM.persist_kind_throttled(self._c(), enabled=True))
            self.assertFalse(self.TM.persist_kind_throttled(self._c(), enabled=True), "20min > 3bar(15min) 应放行")

    def test_other_kind_or_symbol_not_throttled(self):
        with mock.patch.object(self.TM, "_bar_key", return_value="202601091000"):
            self.assertFalse(self.TM.persist_kind_throttled(self._c(), enabled=True))
            self.assertFalse(self.TM.persist_kind_throttled(self._c(sym="SZ002195"), enabled=True))
            self.assertFalse(self.TM.persist_kind_throttled(self._c(kind="high_sell"), enabled=True))

    def test_non_persist_kind_never_throttled(self):
        with mock.patch.object(self.TM, "_bar_key", return_value="202601091000"):
            self.assertFalse(self.TM.persist_kind_throttled({"symbol": "X", "trigger_kind": "low_buy"}, enabled=True))

    def test_stats_exposed(self):
        st = self.TM.persist_throttle_stats()
        for k in ("enabled_cooldown", "enabled_throttle", "throttle_bars", "eval", "have_lt",
                  "cooldown_skip", "throttle_skip"):
            self.assertIn(k, st)

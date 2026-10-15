# -*- coding: utf-8 -*-
"""「只做龙头」通道单测（账本 §9.876 ✓）。

依据：狼大 2025-07-29「下跌浪只做龙头」／2025-05-19「买就买强的 千万不要买后排补涨」。
契约（本测试锁死）：
  ① 开关 `WOLF_PICK_LEADER_ONLY` **默认关** ⇒ 调用方逐字不变 ✓；
  ② **判不了**（无 as-of 日线 ＝ 生产常态 ✓）⇒ `rank_leaders` 返回 `None` ⇒ 调用方**保持原判定** ✓（fail-open ✓）；
  ③ **无合格龙头** ⇒ 返回 `[]` ⇒ 调用方应**不布腿** ✓（绝不退后排 ✓）；
  ④ 合格者按**龙头度**降序 ✓（排序不看正宗度 ✓ —— 实测正宗度无效且反向 ✗）。
"""
from __future__ import annotations

import os
import sys
import unittest

_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "main_line")
sys.path.insert(0, os.path.abspath(_APP))

import theme_leader_score as TLS  # noqa: E402


class TestLeaderOnlySwitch(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("WOLF_PICK_LEADER_ONLY")
        os.environ.pop("WOLF_PICK_LEADER_ONLY", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("WOLF_PICK_LEADER_ONLY", None)
        else:
            os.environ["WOLF_PICK_LEADER_ONLY"] = self._saved

    def test_default_off(self):
        self.assertFalse(TLS.leader_only(), "默认必须关 ⇒ 生产/回测逐字不变")

    def test_env_on(self):
        for v in ("1", "true", "YES", "on"):
            os.environ["WOLF_PICK_LEADER_ONLY"] = v
            self.assertTrue(TLS.leader_only(), "WOLF_PICK_LEADER_ONLY=%r 应开" % v)
        for v in ("0", "false", "no", ""):
            os.environ["WOLF_PICK_LEADER_ONLY"] = v
            self.assertFalse(TLS.leader_only(), "WOLF_PICK_LEADER_ONLY=%r 应关" % v)


class TestRankLeaders(unittest.TestCase):
    def setUp(self):
        self._ld, self._sf = TLS._leaders, TLS.score_for

    def tearDown(self):
        TLS._leaders, TLS.score_for = self._ld, self._sf

    def test_no_bars_returns_none(self):
        """无 as-of 日线（生产常态）⇒ 判不了 ⇒ None（调用方保持原判定 ✓）。"""
        TLS._leaders = lambda theme: {}
        self.assertIsNone(TLS.rank_leaders("AI/算力/科技", ["SZ002156"], day="20251231"))

    def test_all_unscored_returns_none(self):
        """有成员但票票都取不到分 ⇒ 仍算"判不了" ⇒ None（不因数据残缺而清空主题 ✗）。"""
        TLS._leaders = lambda theme: {"002156.SZ": {"amt": 1.0, "mv": 1.0}}
        TLS.score_for = lambda s, t, concepts=None: (None, "无 as-of 日线")
        self.assertIsNone(TLS.rank_leaders("AI/算力/科技", ["SZ002156", "SH688256"]))

    def test_none_qualified_returns_empty(self):
        """全员低于阈值 ⇒ []（调用方**不布腿** ✓）。"""
        TLS._leaders = lambda theme: {"002156.SZ": {"amt": 1.0, "mv": 1.0}}
        TLS.score_for = lambda s, t, concepts=None: (0.10, "太低")
        self.assertEqual(TLS.rank_leaders("AI/算力/科技", ["SZ002156", "SH688256"]), [])

    def test_qualified_sorted_by_lead_desc(self):
        """合格者按**龙头度**降序 ✓；不合格者被剔除 ✓。"""
        TLS._leaders = lambda theme: {
            "002156.SZ": {"amt": 10.0, "mv": 10.0},   # 龙头度最高 ✓
            "688256.SH": {"amt": 5.0, "mv": 5.0},
            "002392.SZ": {"amt": 1.0, "mv": 1.0},     # 杂毛：分不合格 ⇒ 剔除 ✓
        }
        sc = {"SZ002156": 0.70, "SH688256": 0.65, "SZ002392": 0.14}
        TLS.score_for = lambda s, t, concepts=None: (sc.get(s, 0.0), "stub")
        out = TLS.rank_leaders("AI/算力/科技", ["SZ002392", "SZ002156", "SH688256"], day="20251231")
        self.assertEqual([s for s, _v in out], ["SZ002156", "SH688256"], "应按龙头度降序且剔除杂毛")
        self.assertGreater(out[0][1], out[1][1])

    def test_day_is_isolated(self):
        """`day=` 只在本调用内生效 ⇒ 用完 env 必须还原 ✓（不污染后续调用/他人 ✗）。"""
        os.environ.pop("WOLF_ASOF_DAY", None)
        TLS._leaders = lambda theme: {}
        TLS.rank_leaders("AI/算力/科技", ["SZ002156"], day="20251231")
        self.assertIsNone(os.environ.get("WOLF_ASOF_DAY"), "WOLF_ASOF_DAY 必须被还原 ✓")


if __name__ == "__main__":
    unittest.main()

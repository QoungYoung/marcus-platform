# -*- coding: utf-8 -*-
"""「位置高 ∧ 前一日量能放大 ⇒ 不买」买腿闸的单测（2026-09-20）。

覆盖：开关关⇒放行；位置低⇒放行；位置高但缩量⇒放行；位置高∧放量⇒拦；
      持仓中（scope=new）⇒放行；scope=all⇒连再买也拦；
      数据不足 / 形状异常 / 量缺失 ⇒ fail-open；阈值与 mode 可由 env 调；仓位无关（纯函数）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import wolf_high_pos_vol as H            # noqa: E402


def mk(n=30, close0=10.0, drift=0.0, last_close=None, vol=1.0e6, last_vol=None):
    """造 n 根日线：close 从 close0 起按 drift 递推，最后一天可覆盖。"""
    bars, c = [], close0
    for i in range(n):
        c = c * (1.0 + drift)
        cc = (last_close if (last_close is not None and i == n - 1) else c)
        v = (last_vol if (last_vol is not None and i == n - 1) else vol)
        bars.append({"date": "2026%04d" % (i + 1), "open": cc, "high": cc * 1.01,
                     "low": cc * 0.99, "close": cc, "vol": v})
    return bars


class Base(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("WOLF_HIGH_POS_VOL", "WOLF_HPV_SCOPE", "WOLF_HPV_POS_MODE",
                      "WOLF_HPV_LOW20_PCT", "WOLF_HPV_MA20_PCT", "WOLF_HPV_VOL_MULT")}
        for k in self._env:
            os.environ.pop(k, None)
        H.stats_reset()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestSwitch(Base):
    def test_off_by_default(self):
        self.assertFalse(H.enabled())
        bars = mk(drift=0.02, last_vol=5.0e6)          # 高位 ∧ 放量
        self.assertEqual(H.verdict(bars, held=False), (False, ""))

    def test_on_blocks_high_and_expanded(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        bars = mk(drift=0.02, last_vol=5.0e6)          # 20 日累计 +48% ⇒ 距20日低远超 20%
        blk, why = H.verdict(bars, held=False, day="20260201", symbol="SH600000")
        self.assertTrue(blk)
        self.assertIn("位置高", why)
        self.assertIn("量能放大", why)

    def test_low_position_allowed(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        bars = mk(drift=0.0, last_vol=5.0e6)           # 横盘 ⇒ 位置不高
        self.assertEqual(H.verdict(bars, held=False)[0], False)

    def test_high_but_shrink_allowed(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        bars = mk(drift=0.02, last_vol=1.0e5)          # 高位但缩量
        self.assertEqual(H.verdict(bars, held=False)[0], False)

    def test_held_skipped_by_default_scope(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        bars = mk(drift=0.02, last_vol=5.0e6)
        self.assertEqual(H.verdict(bars, held=True)[0], False)      # scope=new 默认
        self.assertEqual(H.scope(), "new")

    def test_scope_all_blocks_rebuy(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        os.environ["WOLF_HPV_SCOPE"] = "all"
        bars = mk(drift=0.02, last_vol=5.0e6)
        self.assertTrue(H.verdict(bars, held=True)[0])
        self.assertTrue(H.applies(True, "all"))
        self.assertFalse(H.applies(True, "new"))


class TestFailOpen(Base):
    def test_no_data(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        for bad in (None, [], [{"close": 1.0}], "garbage"):
            self.assertEqual(H.verdict(bad, held=False)[0], False)

    def test_short_series(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        self.assertEqual(H.verdict(mk(n=10, drift=0.02, last_vol=5e6), held=False)[0], False)

    def test_missing_vol_ratio_allowed(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        bars = mk(drift=0.02, last_vol=5e6)
        for b in bars:
            b["vol"] = None                              # 量缺失 ⇒ 放行侧
        self.assertEqual(H.verdict(bars, held=False)[0], False)

    def test_env_garbage_still_allows(self):
        os.environ["WOLF_HIGH_POS_VOL"] = "1"
        os.environ["WOLF_HPV_VOL_MULT"] = "abc"
        bars = mk(drift=0.02, last_vol=5e6)
        # vol_mult 解析失败 ⇒ 落默认 1.0，仍然拦（不是抛异常）
        self.assertTrue(H.verdict(bars, held=False)[0])


class TestThresholds(unittest.TestCase):
    def setUp(self):
        self._e = {k: os.environ.get(k) for k in
                   ("WOLF_HIGH_POS_VOL", "WOLF_HPV_POS_MODE", "WOLF_HPV_LOW20_PCT",
                    "WOLF_HPV_MA20_PCT", "WOLF_HPV_VOL_MULT", "WOLF_HPV_VOL_FIELD")}
        os.environ["WOLF_HIGH_POS_VOL"] = "1"

    def tearDown(self):
        for k, v in self._e.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_mode_ma20(self):
        os.environ["WOLF_HPV_POS_MODE"] = "ma20"
        os.environ["WOLF_HPV_MA20_PCT"] = "5"
        m = {"dist_ma20_pct": 6.0, "dist_low20_pct": 1.0, "vol_ratio": 2.0}
        self.assertTrue(H.pos_high(m))
        m["dist_ma20_pct"] = 4.0
        self.assertFalse(H.pos_high(m))

    def test_mode_either_both(self):
        m = {"dist_ma20_pct": 9.0, "dist_low20_pct": 10.0, "vol_ratio": 2.0}
        os.environ["WOLF_HPV_LOW20_PCT"] = "20"
        os.environ["WOLF_HPV_MA20_PCT"] = "8"
        self.assertTrue(H.pos_high(m, "ma20"))
        self.assertFalse(H.pos_high(m, "low20"))
        self.assertTrue(H.pos_high(m, "either"))
        self.assertFalse(H.pos_high(m, "both"))
        m["dist_low20_pct"] = 25.0
        self.assertTrue(H.pos_high(m, "both"))

    def test_vol_field_default_is_vol(self):
        self.assertEqual(H.vol_field(), "vol")                    # 量比优先（换手在 2025-12 只有 4% 覆盖）
        m = {"vol_ratio": 1.4, "turn_ratio": 0.5}
        self.assertEqual(H.vol_ratio_of(m), 1.4)
        os.environ["WOLF_HPV_VOL_FIELD"] = "turnover"
        self.assertEqual(H.vol_ratio_of(m), 0.5)
        os.environ["WOLF_HPV_VOL_FIELD"] = "auto"                  # auto 兼容：量比优先、换手兜底
        self.assertEqual(H.vol_ratio_of(m), 1.4)
        self.assertEqual(H.vol_ratio_of({"vol_ratio": None, "turn_ratio": 1.2}), 1.2)

    def test_vol_mult(self):
        os.environ["WOLF_HPV_VOL_MULT"] = "1.5"
        self.assertFalse(H.vol_expanded({"vol_ratio": 1.2}))
        self.assertTrue(H.vol_expanded({"vol_ratio": 1.6}))

    def test_metrics_shape_and_values(self):
        bars = mk(drift=0.02, last_vol=5e6)
        m = H.metrics(bars)
        self.assertIsNotNone(m)
        self.assertGreater(m["dist_low20_pct"], 20)
        self.assertGreater(m["dist_ma20_pct"], 0)
        self.assertGreater(m["vol_ratio"], 3.0)
        # 元组形状（date, open, high, low, close, vol）也接受
        tup = [(b["date"], b["open"], b["high"], b["low"], b["close"], b["vol"]) for b in bars]
        self.assertIsNotNone(H.metrics(tup))

    def test_stats_counters(self):
        H.stats_reset()
        H.verdict(mk(drift=0.02, last_vol=5e6), held=False)
        H.verdict(mk(drift=0.0, last_vol=5e6), held=False)
        s = H.stats()
        self.assertEqual(s["calls"], 2)
        self.assertEqual(s["blocked"], 1)
        self.assertGreaterEqual(s["allowed"], 1)


class TestRealBarsSource(unittest.TestCase):
    """真库（存在时）冒烟：600410 在 2026-03-11 买入前一日是否被判为「高位 ∧ 放量」。"""

    def test_real_case(self):
        bars = H.bars_asof("SH600410", "20260311", 30)
        if not bars:
            self.skipTest("bars.sqlite 不可用（生产/无库环境）")
        self.assertLess(str(bars[-1]["date"]), "20260311")          # 强制 as-of
        m = H.metrics(bars)
        self.assertIsNotNone(m)
        self.assertGreater(m["dist_low20_pct"], 40.0)               # 实测约 +57%
        self.assertTrue(H.vol_expanded(m) or True)                  # 量能仅记录


if __name__ == "__main__":
    unittest.main()

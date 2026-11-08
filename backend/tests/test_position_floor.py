# -*- coding: utf-8 -*-
"""仓位目标 / 下限（`WOLF_POSITION_FLOOR`）回归测试 —— 2026-09-22。

## 为什么

语料里**仓位是目标/下限**，而我们所有容量件都只有**上限**语义
（`_get_total_cap` 75/50/30、单票 cap、现金保留线 25%）⇒ 实测 T5/T6/T8
**≥65% 天数 0/75**、均值 24.5 / 15.8 / 10.7%，与语料「收盘仓位不低于65%」差 40pp+。

## 语料

- 2026-09-03「所以我这里**仓位不会低于65%收盘**，日内做T仓位20%」（`docs/wolf-daily-log-nga.md:925`）
- 2026-01-17「**主升趋势就75%以上**…**调整就50%**…**有风险就30%**…**下跌趋势就不做**」
- 2026-02-05「对啊 **只要当天收盘没有跌破前一天低点 都是70%仓位** 没走弱不用减仓」
  （`docs/PRODUCTION_PIPELINE.md:240` 自记 **❌ 未落**）

## 本测试守什么

1. **默认关（未设/0）⇒ 逐字零变化**：`evaluate` 不取数、floor 与关前一致；
2. **档位 → 目标总仓**：main_up 75 / range 50 / high 30 / **exit 恒 0**（「下跌不做」，不套 70% 条款）；
3. **70% 条款**：`not_broken_prev_low=True` ⇒ range 50→**70**、high 30→**70**、main_up 75 不变、exit 仍 0；
   判据不可用（`None`）⇒ **不套用**（fail-safe，不臆造）；
4. **只抬不压**：`apply_position_floor` 结果 ≥ 原 ratio；总仓 ≥ 目标 ⇒ 原样返回；
5. **指数判据**：`当日收盘 ≥ 前一日最低` ⇒ True；跌破 ⇒ False；文件缺失/不足两根 ⇒ None；
6. **fail-safe**：仓位/目标不可用、非法值 ⇒ 原样返回，绝不因异常放大或乱压；
7. **端到端**：开关开 + 总仓低于目标 ⇒ floor 抬高、可卖 T 仓缩小（`evaluate` 层验证）。
"""
import ast
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import t_base_floor as bf  # noqa: E402


class PositionTargetTest(unittest.TestCase):
    """档位 → 目标总仓（语料映射）。"""

    def test_bucket_mapping(self):
        self.assertEqual(bf.position_target_pct("main_up"), 75.0)
        self.assertEqual(bf.position_target_pct("range"), 50.0)
        self.assertEqual(bf.position_target_pct("high"), 30.0)
        self.assertEqual(bf.position_target_pct("exit"), 0.0)

    def test_unknown_bucket_defaults_range(self):
        self.assertEqual(bf.position_target_pct(""), 50.0)
        self.assertEqual(bf.position_target_pct("nonsense"), 50.0)
        self.assertEqual(bf.position_target_pct(None), 50.0)

    def test_prev_low_clause_raises_to_70(self):
        self.assertEqual(bf.position_target_pct("range", True), 70.0)
        self.assertEqual(bf.position_target_pct("high", True), 70.0)     # 30 → 70
        self.assertEqual(bf.position_target_pct("main_up", True), 75.0)  # 75 不动
        self.assertEqual(bf.position_target_pct("exit", True), 0.0)      # 下跌不做：不套条款

    def test_prev_low_unknown_does_not_apply(self):
        """判据不可用（None）⇒ 不套用 70%（fail-safe，不臆造）。"""
        self.assertEqual(bf.position_target_pct("range", None), 50.0)
        self.assertEqual(bf.position_target_pct("high", None), 30.0)

    def test_pct_override(self):
        self.assertEqual(bf.position_target_pct("range", True, pct=80), 80.0)
        self.assertEqual(bf.position_target_pct("range", True, pct=40), 50.0)   # 只抬不压


class ApplyPositionFloorTest(unittest.TestCase):

    def test_noop_when_exposure_at_or_above_target(self):
        r, why = bf.apply_position_floor(0.2, 60.0, 50.0)
        self.assertEqual(r, 0.2)
        self.assertIn("未介入", why)

    def test_raises_keep_when_below_target(self):
        r, why = bf.apply_position_floor(0.0, 24.5, 70.0)
        self.assertAlmostEqual(r, 0.5)          # WOLF_POSITION_FLOOR_KEEP 默认 0.5
        self.assertIn("仓位下限", why)

    def test_only_raise(self):
        for ratio in (0.0, 0.1, 1.0 / 3.0, 0.5, 2.0 / 3.0, 1.0):
            r, _ = bf.apply_position_floor(ratio, 10.0, 70.0)
            self.assertGreaterEqual(r, ratio, f"ratio={ratio} 被压低了")

    def test_exit_target_zero_is_noop(self):
        """下跌不做（目标 0）⇒ 不介入（不能拿"仓位下限"去对抗清仓）。"""
        r, why = bf.apply_position_floor(0.0, 0.0, 0.0)
        self.assertEqual(r, 0.0)
        self.assertIn("目标 0", why)

    def test_failsafe_on_missing_inputs(self):
        for ex, tg in ((None, 70.0), (24.5, None), (None, None)):
            r, why = bf.apply_position_floor(0.3, ex, tg)
            self.assertEqual(r, 0.3)
            self.assertIn("未介入", why)
        r, _ = bf.apply_position_floor(0.3, "x", 70.0)
        self.assertEqual(r, 0.3)
        r, _ = bf.apply_position_floor("x", 10.0, 70.0)   # ratio 非法 → 当 0
        self.assertAlmostEqual(r, 0.5)

    def test_keep_override_and_clamp(self):
        self.assertAlmostEqual(bf.apply_position_floor(0.0, 10.0, 70.0, keep=1.0)[0], 1.0)
        self.assertAlmostEqual(bf.apply_position_floor(0.0, 10.0, 70.0, keep=-3)[0], 0.0)
        self.assertAlmostEqual(bf.apply_position_floor(0.0, 10.0, 70.0, keep=9)[0], 1.0)


class IndexJudgeTest(unittest.TestCase):
    """语料判据：当日收盘 ≥ 前一日最低（**用 low，不是 close**）。"""

    def _csv(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write("trade_date,open,high,low,close,amount\n")
        for d, lo, c in rows:
            f.write(f"{d},{c},{c},{lo},{c},0\n")
        f.close()
        return f.name

    def test_not_broken(self):
        p = self._csv([("2026-02-03", 100.0, 101.0), ("2026-02-04", 100.5, 102.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertTrue(bf.index_not_broken_prev_low("20260204"))   # 102 ≥ 100.5

    def test_broken(self):
        p = self._csv([("2026-02-03", 100.0, 101.0), ("2026-02-04", 105.0, 99.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertFalse(bf.index_not_broken_prev_low("20260204"))  # 99 < 105

    def test_asof_uses_latest_two(self):
        p = self._csv([("2026-02-03", 100.0, 101.0), ("2026-02-04", 105.0, 99.0),
                       ("2026-02-05", 98.0, 110.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertTrue(bf.index_not_broken_prev_low("20260205"))   # 110 ≥ 105
            # as-of 更早 ⇒ 只用当时可见的两根
            self.assertFalse(bf.index_not_broken_prev_low("20260204"))

    def test_missing_file_is_none(self):
        with mock.patch.object(bf, "_IDX_CSV", "/nonexistent/x.csv"):
            self.assertIsNone(bf.index_not_broken_prev_low("20260204"))

    def test_single_row_is_none(self):
        p = self._csv([("2026-02-04", 100.0, 101.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertIsNone(bf.index_not_broken_prev_low("20260204"))


class EvaluateIntegrationTest(unittest.TestCase):
    """evaluate 层：开关开 + 总仓低于目标 ⇒ floor 抬高（可卖 T 仓缩小）。"""

    def _floor(self, pos_floor_on, exposure, target):
        st = {"bucket_override": "exit", "symbols": {}}
        with mock.patch.object(bf, "_POSITION_FLOOR", pos_floor_on), \
             mock.patch.object(bf, "load_state", return_value=st), \
             mock.patch.object(bf, "_cum_buy_volume", return_value=1000), \
             mock.patch.object(bf, "index_not_broken_prev_low", return_value=True), \
             mock.patch.object(bf, "exposure_pct", return_value=exposure), \
             mock.patch.object(bf, "position_target_pct", return_value=target):
            return bf.base_floor_shares("stock", "SH600000", volume=1000, persist=False)

    def test_off_is_unchanged(self):
        """默认关 ⇒ 不介入（floor 与不开时一致：exit 档 ratio 0 ⇒ 落到"一手"下限）。"""
        self.assertEqual(self._floor(False, 10.0, 70.0), bf.MIN_LOT)

    def test_on_raises_floor_when_below_target(self):
        on = self._floor(True, 10.0, 70.0)
        off = self._floor(False, 10.0, 70.0)
        self.assertGreater(on, off, "开关开了但 floor 没抬高")
        # ratio 被抬到 0.5 ⇒ floor ≈ 500，可卖 T 仓从 900 缩到 500
        self.assertGreaterEqual(on, 500)

    def test_on_noop_when_above_target(self):
        on = self._floor(True, 80.0, 70.0)
        self.assertEqual(on, bf.MIN_LOT)

    def test_switch_default_off_in_source(self):
        """开关默认必须 0（生产零影响），且从 env 读。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "app", "services", "t_base_floor.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_POSITION_FLOOR", "0")', src)
        ast.parse(src)


class IndexSourceIntegrityTest(unittest.TestCase):
    """数据源完整性守卫（2026-09-22 踩坑）。

    仓库 `data/指数数据/index_daily/000001.SH.csv` 的 **2026 段 open/high/low 全为空**；
    若不校验，就会拿**上一年**的两根行做常数比较 ⇒ 静默返回恒定的 False
    （实测得出"75 天全跌破前一日低点"这种不可能的结论）。守两件事：
      ① 缺 low 的行必须**跳过**；② 最新一行离 as-of 太远 ⇒ 判据**不可用（None）**。
    """

    def _csv(self, rows, with_low=True):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write("trade_date,open,high,low,close,amount\n")
        for d, lo, c in rows:
            if with_low:
                f.write(f"{d},{c},{c},{lo},{c},0\n")
            else:
                f.write(f"{d},,,,{c},0\n")        # 与仓库 2026 段同形：OHLC 全空
        f.close()
        return f.name

    def test_rows_without_low_are_skipped(self):
        p = self._csv([("2026-02-03", 0, 101.0), ("2026-02-04", 0, 102.0)], with_low=False)
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertIsNone(bf.index_not_broken_prev_low("20260204"),
                              "缺 low 的行没被跳过 ⇒ 会拿更早的行做常数比较")

    def test_stale_source_is_unusable(self):
        """最新行是 2025 年，as-of 是 2026 年 ⇒ 不可用（而不是返回常数）。"""
        p = self._csv([("2025-12-30", 100.0, 101.0), ("2025-12-31", 100.5, 102.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertIsNone(bf.index_not_broken_prev_low("20260204"))

    def test_fresh_window_ok(self):
        p = self._csv([("2026-01-30", 100.0, 101.0), ("2026-02-02", 100.5, 102.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertTrue(bf.index_not_broken_prev_low("20260204"))   # 2 天差 ⇒ 新鲜

    def test_multi_source_fallback(self):
        stale = self._csv([("2025-12-30", 100.0, 101.0), ("2025-12-31", 100.5, 102.0)])
        fresh = self._csv([("2026-02-03", 100.0, 101.0), ("2026-02-04", 105.0, 99.0)])
        self.assertFalse(bf.index_not_broken_prev_low("20260204", sources=[stale, fresh]))
        self.assertFalse(bf.index_not_broken_prev_low("20260204", sources=[fresh, stale]))

    def test_stale_days_param(self):
        p = self._csv([("2026-01-30", 100.0, 101.0), ("2026-02-02", 100.5, 102.0)])
        with mock.patch.object(bf, "_IDX_CSV", p):
            self.assertIsNone(bf.index_not_broken_prev_low("20260204", stale_days=1))
            self.assertTrue(bf.index_not_broken_prev_low("20260204", stale_days=5))


class PositionFloorLatchTest(unittest.TestCase):
    """`WOLF_POSITION_FLOOR_ABS`（latch 式绝对底仓）—— 2026-09-22 第 4 轮新增。

    为什么需要它：只抬 ratio 在**被清过仓的票**上完全空转 —— 清仓写 reset（base=0, cum_at_base=cum）
    ⇒ 该票再次买入前 anchor 恒为 0 ⇒ `floor = max(anchor, MIN_LOT) = 一手`，与 ratio 无关
    （纯函数实测：ratio 0/0.33/0.5/0.67 都得 floor=100）。所以"仓位下限"必须锚在**绝对量**上。
    """

    def _run(self, abs_on, exposure, target, sel=1000, cum=1000):
        # ⚠️ 必须造出**真实现场**：被清过仓的票 = 有 reset 记录（base=0, cum_at_base=cum）
        #    —— 此时 anchor 恒 0 ⇒ floor 落到"一手"，这才是 ratio 空转的那个状态。
        st = {"bucket_override": "range",
              "symbols": {"stock:SH600000": {"base": 0, "cum_at_base": cum, "bucket": "range"}}}
        rec_events = []
        with mock.patch.object(bf, "_POSITION_FLOOR", True), \
             mock.patch.object(bf, "_POSITION_FLOOR_ABS", abs_on), \
             mock.patch.object(bf, "load_state", return_value=st), \
             mock.patch.object(bf, "_cum_buy_volume", return_value=cum), \
             mock.patch.object(bf, "index_not_broken_prev_low", return_value=True), \
             mock.patch.object(bf, "exposure_pct", return_value=exposure), \
             mock.patch.object(bf, "position_target_pct", return_value=target), \
             mock.patch.object(bf, "_record_event",
                               side_effect=lambda *a, **k: rec_events.append(a[2])):
            floor = bf.base_floor_shares("stock", "SH600000", volume=sel, persist=True)
        return floor, rec_events

    def test_off_is_unchanged(self):
        """默认关 ⇒ 逐字旧行为（被清过仓的票：anchor=0 ⇒ floor 落到"一手"下限，ratio 空转）。"""
        off, ev = self._run(False, 10.0, 70.0)
        self.assertEqual(off, bf.MIN_LOT)
        self.assertEqual(ev, [])

    def test_on_latches_absolute_floor(self):
        """开 + 总仓低于目标 ⇒ 底仓 = 可卖 × keep（0.5）⇒ 1000×0.5 = 500。"""
        on, ev = self._run(True, 10.0, 70.0)
        self.assertGreater(on, bf.MIN_LOT)
        self.assertEqual(on, 500)
        self.assertTrue(ev, "没有写认账事件 ⇒ 下次又会退回一手（不是 latch）")
        e = ev[0]
        self.assertEqual(e["kind"], "rebase")
        self.assertEqual(e["floor"], 500)
        self.assertEqual(e["cum_buy"], 1000)          # base 只随新增买入增长
        self.assertEqual(e.get("src"), "position_floor_abs")

    def test_latch_does_not_decay_with_sells(self):
        """咬住之后**不随卖出下降**（否则就是 733→400→200→100 被啃光的老路）。

        模拟：已认账 base=500/cum_at_base=1000，卖到只剩 400 ⇒ floor 仍 = 500。
        """
        rec = {"base": 500, "cum_at_base": 1000}
        # 臂里的真实配置：pins 置 WOLF_BASE_FLOOR_STRICT=1（完全不做向下认账）
        with mock.patch.object(bf, "STRICT_ANCHOR", True), \
             mock.patch.object(bf, "NO_DOWN_REBASE", True):
            f, _ = bf.compute_floor(1000, 400, 0.5, rec=rec, position=400)
            self.assertEqual(f, 500, "认账后的底仓被卖出带下去了")
        # 对照：不设严格锚时，`compute_floor` 会做一次向下认账（历史行为，臂里已由 pins 关掉）
        f2, _ = bf.compute_floor(1000, 400, 0.5, rec=rec, position=400)
        self.assertLessEqual(f2, f, "非严格锚下应允许向下认账（说明严格锚确实在起作用）")

    def test_noop_when_above_target(self):
        on, ev = self._run(True, 90.0, 70.0)
        self.assertEqual(on, bf.MIN_LOT)
        self.assertEqual(ev, [])

    def test_exit_target_zero_is_noop(self):
        on, ev = self._run(True, 0.0, 0.0)
        self.assertEqual(on, bf.MIN_LOT)

    def test_switch_default_off_in_source(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "app", "services", "t_base_floor.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_POSITION_FLOOR_ABS", "0")', src)
        self.assertIn('os.getenv("WOLF_POSITION_FLOOR_KEEP", "0.5")', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

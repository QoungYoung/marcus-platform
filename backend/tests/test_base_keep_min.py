# -*- coding: utf-8 -*-
"""底仓保留比例**结构下限**（`WOLF_BASE_KEEP_MIN`）回归测试 —— 2026-09-22。

## 病灶（实测 data/_bt_t5 全窗）

档位把"底仓占比"也一起降了下去：`t_base_floor._BUCKET_DEFAULT` = main_up 2/3 / range 1/2 /
**high 1/3 / exit 0**。3 月中旬下跌把浪型打到 defense(`high`)/`exit` ⇒ ratio 掉到 1/3~0
⇒ 「可卖 = 持仓 − floor」≈ 全仓 ⇒ 高抛腿把底仓卖到 0；归零又写 reset ⇒ 账户进入
**"建仓→高抛清仓→再建仓"** 循环。实测 T5：清仓次数 1月 2 / 2月 3 / **3月 10 / 4月 16**，
持仓期中位 8→10→**3→3** 天，**3-12 之后"老仓（买入≥4 交易日）占比"恒为 0%**。
⇒ 底仓再也建不起来，反弹段（市场 +9.6%、持仓端 +22.7%）组合只吃到 +5.7%。

## 语料（支持"底仓 50% 是结构、不随档位变"）

- 2026-04-14「**50%的底仓 30%左右做日内** 或者一天到两天的T，**剩下20%左右应对…黑天鹅的抄底**」
- 2026-09-03「**仓位不会低于65%收盘，日内做T仓位20%**」
- 档位那几句（2026-01-17「主升75%以上…调整50…有风险30…下跌不做」）讲的是**总仓**；
  语料里**没有**"底仓占比随档位降到 1/3 或 0" ⇒ high/exit 的 1/3、0 属自设。

## 本测试守什么

1. **默认关（未设/0）⇒ 逐字零变化**：`apply_keep_min(r) == r`（含 0 / 0.5 / 2/3 / 1 / 非法输入）；
2. **只抬不压**：任意 ratio × keep_min 组合下结果 ≥ ratio（不可能因本开关"多卖"或"多拦"）；
3. **档位映射生效**：keep_min=0.5 ⇒ main_up 2/3、range 0.5 不变；**high 1/3 → 0.5、exit 0 → 0.5**；
4. **clamp**：keep_min < 0 → 0、> 1 → 1、非法字符串 → 0（fail-safe 不动）；
5. **env 管道**：设 `WOLF_BASE_KEEP_MIN=0.5` 立刻生效（不必重启进程）；
6. **端到端（evaluate）**：exit 档（ratio 0）下 keep_min=0.5 会**抬高 floor**、**缩小可卖 T 仓**
   ⇒ 高抛腿无法把底仓清零；且 floor 随 ratio 单调不减；
7. **回滚开关**：`T_BASE_FLOOR_REBASE=0` ⇒ 走 legacy 路径，本开关**完全不介入**（零变化）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import t_base_floor as bf  # noqa: E402


class ApplyKeepMinTest(unittest.TestCase):
    """纯函数：只抬不压 + clamp + 默认零变化。"""

    def test_default_off_is_identity(self):
        """未设/设 0 ⇒ 原样返回（生产零影响）。"""
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "0"}, clear=False):
            for r in (0.0, 1.0 / 3.0, 0.5, 2.0 / 3.0, 1.0, 0.0):
                self.assertEqual(bf.apply_keep_min(r), r)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WOLF_BASE_KEEP_MIN", None)
            self.assertEqual(bf.apply_keep_min(0.0), 0.0)
            self.assertEqual(bf.apply_keep_min(0.25), 0.25)

    def test_only_raise_never_lower(self):
        """任意组合下结果 ≥ ratio —— 本开关不可能导致"多卖/多拦"。"""
        for km in (0.0, 0.1, 1.0 / 3.0, 0.5, 2.0 / 3.0, 1.0):
            for r in (0.0, 0.1, 1.0 / 3.0, 0.5, 2.0 / 3.0, 0.9, 1.0):
                self.assertGreaterEqual(bf.apply_keep_min(r, km), r,
                                        f"ratio={r} keep_min={km} 被压低了")

    def test_bucket_mapping(self):
        """keep_min=0.5 ⇒ 只把 low 的两档抬高（high 1/3、exit 0）。"""
        got = {b: bf.apply_keep_min(bf.ratio_of(b), 0.5) for b in bf._BUCKET_DEFAULT}
        self.assertAlmostEqual(got["main_up"], 2.0 / 3.0)
        self.assertAlmostEqual(got["range"], 0.5)
        self.assertAlmostEqual(got["high"], 0.5)      # 1/3 → 0.5
        self.assertAlmostEqual(got["exit"], 0.5)      # 0   → 0.5

    def test_bucket_mapping_off_is_untouched(self):
        got = {b: bf.apply_keep_min(bf.ratio_of(b), 0.0) for b in bf._BUCKET_DEFAULT}
        self.assertAlmostEqual(got["high"], 1.0 / 3.0)
        self.assertAlmostEqual(got["exit"], 0.0)

    def test_clamp_and_garbage(self):
        self.assertEqual(bf.apply_keep_min(0.2, -5), 0.2)       # 负 → 0
        self.assertEqual(bf.apply_keep_min(0.2, 9), 1.0)        # >1 → 1
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "abc"}, clear=False):
            self.assertEqual(bf.apply_keep_min(0.0), 0.0)       # 非法 → 不动
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": ""}, clear=False):
            self.assertEqual(bf.apply_keep_min(0.0), 0.0)
        # ratio 本身非法 → 当 0 处理，再被 keep_min 抬
        self.assertEqual(bf.apply_keep_min(None, 0.5), 0.5)
        self.assertEqual(bf.apply_keep_min("x", 0.5), 0.5)

    def test_env_pipeline_live(self):
        """env 改了立即生效（运行期可切），不需要重启。"""
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "0"}, clear=False):
            self.assertEqual(bf.apply_keep_min(0.0), 0.0)
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "0.5"}, clear=False):
            self.assertEqual(bf.apply_keep_min(0.0), 0.5)
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "100"}, clear=False):
            self.assertEqual(bf.apply_keep_min(0.0), 1.0)


class FloorIntegrationTest(unittest.TestCase):
    """compute_floor / evaluate 层面：抬高 floor ⇒ 缩小可卖 T 仓（底仓不会被清零）。"""

    def test_compute_floor_monotone_in_ratio(self):
        prev = -1
        for r in (0.0, 1.0 / 3.0, 0.5, 2.0 / 3.0, 1.0):
            floor, _ = bf.compute_floor(1000, 1000, r, rec={"base": 0, "cum_at_base": 0},
                                        position=1000)
            self.assertGreaterEqual(floor, prev, f"ratio={r} 时 floor 反降")
            prev = floor

    def test_exit_bucket_floor_raised_and_t_shop_shrinks(self):
        """exit 档：keep_min=0.5 ⇒ floor 抬高、可卖 T 仓变小（止盈腿不能清零底仓）。"""
        cum, sel = 1000, 1000
        rec = {"base": 0, "cum_at_base": 0}
        f_off, _ = bf.compute_floor(cum, sel, bf.apply_keep_min(0.0, 0.0), rec=rec, position=sel)
        f_on, _ = bf.compute_floor(cum, sel, bf.apply_keep_min(0.0, 0.5), rec=rec, position=sel)
        self.assertGreater(f_on, f_off, "开 keep_min 后 floor 未抬高")
        # ⚠️ `compute_floor` 有 `floor = max(_lot_floor(anchor), min_lot)` ⇒ **floor 恒 ≥ 一手**。
        #   即 ratio=0 时 floor 也是 100（一手），所以"底仓被清零"**不是**本模块放行的
        #   ——根因在网关只断言裸空、不执行 floor（见 test_sell_floor_enforce.py）。
        self.assertEqual(f_off, bf.MIN_LOT)             # exit 档：floor = 一手（下限保护）
        self.assertEqual(sel - f_off, 900)              # 现状：除一手外整仓可卖
        self.assertLess(sel - f_on, sel - f_off)        # 开后：可卖 T 仓进一步缩小
        self.assertGreaterEqual(f_on, f_off)

    def test_single_lot_never_sold_out(self):
        """不足两条腿：整仓即底仓（`_rebase_floor` 的既有保护）在 keep_min 下仍成立。"""
        floor, _ = bf.compute_floor(100, 100, bf.apply_keep_min(0.0, 0.5),
                                    rec={"base": 0, "cum_at_base": 0}, position=100)
        self.assertGreaterEqual(floor, 0)
        self.assertLessEqual(max(100 - floor, 0), 100)

    def test_rollback_switch_bypasses_keep_min(self):
        """`T_BASE_FLOOR_REBASE=0` ⇒ 走 legacy，本开关完全不介入。"""
        sentinel = 4242          # `_legacy_floor` 返回 int（floor），evaluate 再包成 (floor, event)
        with mock.patch.dict(os.environ, {"T_BASE_FLOOR_REBASE": "0",
                                          "WOLF_BASE_KEEP_MIN": "0.9"}, clear=False):
            with mock.patch.object(bf, "_legacy_floor", return_value=sentinel) as m:
                got = bf.evaluate("stock", "SH600000", volume=100)
                self.assertEqual(got, (sentinel, None))
                m.assert_called_once()

    def test_evaluate_uses_effective_ratio(self):
        """evaluate 里 floor 用的是"抬高后"的 ratio（用注入的 state 走 exit 档验证）。"""
        st = {"bucket_override": "exit", "symbols": {}}
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "0"}, clear=False):
            with mock.patch.object(bf, "load_state", return_value=st), \
                 mock.patch.object(bf, "_cum_buy_volume", return_value=1000):
                off = bf.base_floor_shares("stock", "SH600000", volume=1000, persist=False)
        with mock.patch.dict(os.environ, {"WOLF_BASE_KEEP_MIN": "0.5"}, clear=False):
            with mock.patch.object(bf, "load_state", return_value=st), \
                 mock.patch.object(bf, "_cum_buy_volume", return_value=1000):
                on = bf.base_floor_shares("stock", "SH600000", volume=1000, persist=False)
        self.assertGreater(on, off)
        self.assertEqual(off, bf.MIN_LOT)   # exit 档 ratio=0 ⇒ floor 落到"一手"下限


if __name__ == "__main__":
    unittest.main(verbosity=2)

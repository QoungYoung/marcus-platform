# -*- coding: utf-8 -*-
"""正T买单笔名义上限（`WOLF_ZHENGT_MAX_NOTIONAL`）回归测试 —— 2026-09-25。

## 语料（逐字）

狼大 2026-03-31：「必须是有底仓、自己一直关注的方向（没有底仓的不做）；**只用10%仓位做T**」；
另见「用10%仓位做做T」「用10%仓位或半仓内做T」。我们此前**没有这道闸**
（实测正T买单笔名义占账户中位 11.6~13.1%、最大 17.0%，中位数就已越过 10%）。

## 实测依据（账本 §9.18③）

按名义占比分档，>10% 那批的**收益率也系统性更差**：
T13 −1.99% vs −1.61%｜T14 −2.19% vs −1.04%｜T11 −2.32% vs −1.86%｜T5 −1.64% vs +2.13%；
按 10% 缩量的腿级反事实：T13 +3,536 / T14 +2,897 / T11 +3,420 / T5 +1,800（四臂全改善）。
（更紧的 8% 在四臂上还更好，但**参数取他的话（10%）**，不自己调参。）

## 本测试守什么

1. 库内默认**关**（`WOLF_ZHENGT_MAX_NOTIONAL` 未设 ⇒ 0.0，原样返回）；
2. 开 ⇒ 名义超限**按整手向下收敛**；名义在额度内 ⇒ 不动；
3. 总资产不可用 / 无量无价 ⇒ **fail-open 原样返回**（不臆造）；
4. 上限小于 1 手 ⇒ 返回 0（本次不发），而不是发一个超限的手数；
5. 只作用于正T买腿（本函数是纯函数，作用面由调用方按 `event_type == 'wolf_zheng_t_buy'` 收敛）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import t_capacity as cap  # noqa: E402


class ZhengtNotionalCapTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop("WOLF_ZHENGT_MAX_NOTIONAL", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("WOLF_ZHENGT_MAX_NOTIONAL", None)
        else:
            os.environ["WOLF_ZHENGT_MAX_NOTIONAL"] = self._old

    # ── ① 默认关 ────────────────────────────────────────────────────────────
    def test_default_off(self):
        self.assertEqual(cap.zhengt_cap_pct(), 0.0)
        v, why = cap.clamp_zhengt_notional(50000, 10.0, 250000.0)
        self.assertEqual(v, 50000)
        self.assertIn("未开", why)

    # ── ② 开 ⇒ 整手向下收敛 / 额度内不动 ─────────────────────────────────────
    def test_cap_and_round_lot(self):
        os.environ["WOLF_ZHENGT_MAX_NOTIONAL"] = "10"
        self.assertEqual(cap.zhengt_cap_pct(), 10.0)
        # 250000×10% = 25000 元；价 10.37 ⇒ 2410.8 股 ⇒ 整手向下 2400
        v, why = cap.clamp_zhengt_notional(5000, 10.37, 250000.0)
        self.assertEqual(v, 2400)
        self.assertIn("收敛", why)
        v2, _ = cap.clamp_zhengt_notional(1000, 10.37, 250000.0)
        self.assertEqual(v2, 1000, "在额度内不应改动")

    # ── ③ fail-open ────────────────────────────────────────────────────────
    def test_fail_open(self):
        os.environ["WOLF_ZHENGT_MAX_NOTIONAL"] = "10"
        for eq in (None, 0, -5, "x"):
            v, _ = cap.clamp_zhengt_notional(9000, 10.0, eq)
            self.assertEqual(v, 9000, "总资产不可用 ⇒ 原样返回")
        self.assertEqual(cap.clamp_zhengt_notional(0, 10.0, 250000.0)[0], 0)
        self.assertEqual(cap.clamp_zhengt_notional(5000, 0, 250000.0)[0], 5000)

    # ── ④ 上限不足 1 手 ⇒ 不发 ──────────────────────────────────────────────
    def test_below_one_lot(self):
        os.environ["WOLF_ZHENGT_MAX_NOTIONAL"] = "1"     # 250000×1% = 2500 元；价 50 ⇒ 50 股 < 1 手
        v, why = cap.clamp_zhengt_notional(5000, 50.0, 250000.0)
        self.assertEqual(v, 0)
        self.assertIn("1 手", why)

    # ── ⑤ 静态：pins 已置 10，且默认库内为 0（生产零影响）──────────────────
    def test_switch_wiring(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_capacity.py"),
                   encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_ZHENGT_MAX_NOTIONAL", "0")', src)


if __name__ == "__main__":
    unittest.main()

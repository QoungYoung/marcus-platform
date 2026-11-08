# -*- coding: utf-8 -*-
"""B 闸 v2 回归：**能区分"强势回踩"与"真缓跌"**，且豁免条件全部来自狼大原话。

v1（`falling`）只用当日分时三量：现价 < VWAP ∧（自开盘逐根走低 ∨ 跌破当日开盘）
⇒ 完全不看日线 ⇒「强势股回踩」与「真缓跌」在它眼里同形（实测环旭电子 601231 2026-02-10
被拦 28 条买腿，两天后 +10.9%、全月 +40.6%）。

v2 = v1 骨架 + 四类**语料买点**豁免：
  E1 到线（「跌到事先画好的线上（13/34/60/144）」xls2025:257 / 「上下没到线…继续挂单等着」:182）
  E2 到前低（254「破前低 + 缩量」/「跌下来直接补平」xls2016:245）
  E3 破一下当天拉回（「不破 60 日线或破一下当天拉回」nga:1873）
  E4 急杀（「急杀可以买，缓跌不买」2025-06-05 / 「急杀、某些票到线→到线就长线配置」:266）
**不引入任何他没说过的量**（相对强度/位置分位都不加）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "apps", "main_line")):
    sys.path.insert(0, p)

import intraday_crush as ic  # noqa: E402


def F(px=37.9, vwap=38.9, op=39.74, down=True, below=True, lo=37.1, vr=1.0, drop2=None, pl=37.2):
    return {"px": px, "vwap": vwap, "open": op, "below_open": below, "nb_down": down,
            "last3_down": down, "day_lo": lo, "day_hi": 39.93, "vr": vr,
            "last2_drop_pct": drop2, "prev_low": pl}


class FallingV2Test(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get(ic._V2_ENV)
        os.environ.pop(ic._V2_ENV, None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop(ic._V2_ENV, None)
        else:
            os.environ[ic._V2_ENV] = self._old

    # ── ① 形状不坏 ⇒ 两版都不介入 ──────────────────────────────────────────
    def test_shape_ok_no_block(self):
        f = F(px=39.5, vwap=38.9, below=False)
        self.assertEqual(ic.falling_v2(f), (False, ""))

    # ── ② 真缓跌（形状坏 + 四类买点都不命中）⇒ 拦 ──────────────────────────
    def test_pure_slow_bleed_blocked(self):
        f = F(px=37.9, vwap=38.9, lo=37.85, pl=35.0, vr=0.9, drop2=0.2)
        bad, why = ic.falling_v2(f, {"lines": [34.5, 33.0, 32.0, 31.0], "prev_low": 35.0})
        self.assertTrue(bad, "真缓跌应拦")
        self.assertIn("未命中任何买点", why)
        self.assertIn("语料 2025-06-05", why)

    # ── ③ E1 到线 ⇒ 放行 ───────────────────────────────────────────────────
    def test_exempt_e1_line(self):
        f = F(px=37.9, vwap=38.9, lo=37.85, pl=30.0)
        bad, why = ic.falling_v2(f, {"lines": [37.7], "prev_low": 30.0})
        self.assertFalse(bad)
        self.assertIn("E1 到线", why)

    # ── ④ E2 到前低 ⇒ 放行 ─────────────────────────────────────────────────
    def test_exempt_e2_prev_low(self):
        f = F(px=37.9, vwap=38.9, lo=37.85, pl=37.6)      # 距前低 0.8% ≤ 1% 容差
        bad, why = ic.falling_v2(f, {"lines": [30.0], "prev_low": 37.6})
        self.assertFalse(bad)
        self.assertIn("E2 到前低", why)

    def test_tolerance_boundary(self):
        """容差是"到线/到前低"的判定边界：1% 内放行、超出则不放行（且此时若形状坏 ⇒ 拦）。"""
        f = F(px=37.9, vwap=38.9, lo=37.85, pl=37.4)      # 距前低 1.34% > 1%
        bad, _why = ic.falling_v2(f, {"lines": [30.0], "prev_low": 37.4})
        self.assertTrue(bad, "超出容差 ⇒ 不算到前低")
        f2 = F(px=37.9, vwap=38.9, lo=37.85, pl=37.6)
        bad2, _ = ic.falling_v2(f2, {"lines": [30.0], "prev_low": 37.6})
        self.assertFalse(bad2, "容差内 ⇒ 到前低")

    # ── ⑤ E3 破一下当天拉回 ⇒ 放行 ─────────────────────────────────────────
    def test_exempt_e3_reclaim(self):
        f = F(px=37.9, vwap=38.9, lo=37.0, pl=35.0)     # 当日低 37.0 破了线 37.5，现价 37.9 已收回
        bad, why = ic.falling_v2(f, {"lines": [37.5], "prev_low": 35.0})
        self.assertFalse(bad)
        self.assertIn("E3 破一下当天拉回", why)

    # ── ⑥ E4 急杀（近 2 根跌 + 放量）⇒ 放行；量不放大 ⇒ 不豁免 ────────────
    def test_exempt_e4_crash_needs_volume(self):
        f = F(px=37.9, vwap=38.9, lo=37.85, pl=30.0, vr=2.0, drop2=2.5)
        bad, why = ic.falling_v2(f, {"lines": [30.0], "prev_low": 30.0})
        self.assertFalse(bad, "急杀+放量应放行")
        self.assertIn("E4 急杀", why)
        f2 = F(px=37.9, vwap=38.9, lo=37.85, pl=30.0, vr=0.8, drop2=2.5)
        bad2, _ = ic.falling_v2(f2, {"lines": [30.0], "prev_low": 30.0})
        self.assertTrue(bad2, "缩量下跌不算急杀（他：只要看见没量就别追）")

    # ── ⑦ 缺线数据 ⇒ 退化为 v1 行为（不额外放行，fail-safe） ──────────────
    def test_missing_lines_degrade_to_v1(self):
        f = F(px=37.9, vwap=38.9, lo=37.85, pl=30.0)
        bad, why = ic.falling_v2(f, {})
        self.assertTrue(bad, "没有线数据 ⇒ 与 v1 同（拦）")
        self.assertIn("无线数据", why)

    # ── ⑧ 开关与接线 ───────────────────────────────────────────────────────
    def test_switch_default_off_and_wiring(self):
        self.assertFalse(ic.falling_v2_on())
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("_icg.falling_v2_on()", src)
        self.assertIn("falling_v2(", src)
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_FALLING_GATE_V2", pins)

    # ── ⑨ 线工具：13/34/60/144 四条（他的话） ─────────────────────────────
    def test_line_values_uses_his_lines(self):
        import wolf_ma_line_entry as ml
        self.assertEqual(tuple(ml.LINES), (13, 34, 60, 144))
        closes = [float(i) for i in range(1, 201)]
        lines = [sum(closes[-k:]) / k for k in ml.LINES]
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0] > lines[3], "13 日均线应高于 144 日（上升序列）")

    # ── ⑩ 与语料一致性：v2 不新增"他没说过的"判据 ─────────────────────────
    def test_no_extra_criteria_in_source(self):
        src = open(os.path.join(ROOT, "apps", "main_line", "intraday_crush.py"), encoding="utf-8").read()
        seg = src[src.index("def falling_v2("):src.index("def weak_day(")]
        # 跳过 docstring：只在**代码体**里查（docstring 里会写"不引入相对强度/分位"这类说明）
        _doc_end = seg.find('"""', seg.find('"""') + 3)
        body = seg[_doc_end + 3:] if _doc_end > 0 else seg
        for banned in ("相对强度", "分位", "rs_rank", "index_ret"):
            self.assertNotIn(banned, body, "v2 代码体不得引入语料之外的量：%s" % banned)
        for cite in ("13/34/60/144", "破一下当天拉回", "急杀可以买，缓跌不买", "254"):
            self.assertIn(cite, seg, "v2 缺语料出处标注：%s" % cite)


if __name__ == "__main__":
    unittest.main()

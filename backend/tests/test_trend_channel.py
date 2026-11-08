# -*- coding: utf-8 -*-
"""趋势/突破通道（trend_channel）单测。

语料与设计见 apps/main_line/trend_channel.py 头；本文件只测**纯函数**与开关语义：
  · 开关默认关（生产零影响）
  · T1 带量突破 / T2 站稳 / T3 次日发腿（结构上） / T5 高位放量滞涨否证
  · 表达式结构（不追高上限）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import trend_channel as T  # noqa: E402


def series_breakout(hold_days=1, drop_pct=0.0, vol_mult=3.0, pos_high=False):
    """造一段"平台 → 带量突破 → 站稳 hold_days 天"的序列。

    返回 (closes, vols)
    """
    closes = [10.0 + 0.001 * i for i in range(30)]          # 平台（微升，避免突破门槛失真）
    base = closes[-1]
    bk = round(base * 1.03, 3)                              # 突破日：+3%（> 1.005×前 20 日最高）
    closes.append(bk)
    vols = [100.0 + (i % 5) for i in range(30)] + [100.0 * vol_mult]   # 平台带噪声 + 突破日放量
    px = bk
    for i in range(hold_days):
        px = px * (1.0 - drop_pct)
        closes.append(round(px, 3))
        vols.append(200.0 if pos_high else 90.0)
    return closes, vols


class TestSyntaxAndSwitch(unittest.TestCase):
    def test_default_off(self):
        os.environ.pop("WOLF_TREND_CHANNEL", None)
        self.assertFalse(T.enabled(), "库内默认必须关（生产零影响）")

    def test_on(self):
        os.environ["WOLF_TREND_CHANNEL"] = "1"
        try:
            self.assertTrue(T.enabled())
        finally:
            os.environ.pop("WOLF_TREND_CHANNEL", None)

    def test_scan_off_returns_empty(self):
        os.environ.pop("WOLF_TREND_CHANNEL", None)
        self.assertEqual(T.scan("20260112", symbols=["SH600183"]), [])


class TestStage(unittest.TestCase):
    def test_breakout_then_stand(self):
        c, v = series_breakout(hold_days=1, drop_pct=0.005)
        r = T.stage(c, v)
        self.assertEqual(r["stage"], T.STAGE_BREAK, r.get("desc"))
        self.assertAlmostEqual(r["level"], c[30], places=3)   # 突破日收盘 = 买入参考位

    def test_breakout_no_volume(self):
        c, v = series_breakout(hold_days=1, vol_mult=1.0)   # 量能 z≈0
        r = T.stage(c, v)
        self.assertEqual(r["stage"], "", "带量不足不应算突破")

    def test_breakout_then_fail_stand(self):
        c, v = series_breakout(hold_days=1, drop_pct=0.06)  # 次日跌 6% < 0.97
        r = T.stage(c, v)
        self.assertEqual(r["stage"], "")
        self.assertIn("T2", r["desc"])

    def test_breakout_too_old(self):
        c, v = series_breakout(hold_days=5, drop_pct=0.0)
        r = T.stage(c, v)
        self.assertEqual(r["stage"], "", "超过 stand_days 的突破不再发腿")

    def test_veto_high_pos_vol(self):
        # 突破后连续放量、且位置分位高 ⇒ T5 否证
        c, v = series_breakout(hold_days=2, drop_pct=0.0, pos_high=True)
        r = T.stage(c, v)
        self.assertTrue(r.get("signals", {}).get("veto_high_pos_vol") or r["stage"] == "",
                        "高位放量滞涨应被否证")

    def test_sample_too_short(self):
        r = T.stage([1.0] * 10, [1.0] * 10)
        self.assertEqual(r["stage"], "")


class TestExpr(unittest.TestCase):
    def test_expr_bounds(self):
        e = T.expr(100.0, chase_max=0.03)
        vals = [x["value"] for x in e["and"] if x["field"] == "quote.current"]
        self.assertIn(100.0, vals)
        self.assertIn(103.0, vals)

    def test_expr_default_chase(self):
        e = T.expr(50.0)
        vals = sorted(x["value"] for x in e["and"] if x["field"] == "quote.current")
        self.assertEqual(vals[0], 50.0)
        self.assertAlmostEqual(vals[1], 51.5, places=3)



class TestConceptLeaders(unittest.TestCase):
    """「各概念排龙头」（2026-09-21 用户拍板）：不能把 37 个概念混在一起排。"""

    def _dom(self):
        # 概念 A：小盘概念，X 是组内最强但放到全局很弱；概念 B：大盘概念，强者如云
        return [
            {"symbol": "SZ000001", "concepts": ["A"], "r60": 5.0, "amt20": 10.0, "lim": 0, "_stage": {"stage": T.STAGE_BREAK, "level": 1.0}},
            {"symbol": "SZ000002", "concepts": ["A"], "r60": -20.0, "amt20": 2.0, "lim": 0, "_stage": {"stage": "", "level": None}},
            {"symbol": "SZ000003", "concepts": ["A"], "r60": -30.0, "amt20": 1.0, "lim": 0, "_stage": {"stage": "", "level": None}},
            {"symbol": "SZ000011", "concepts": ["B"], "r60": 300.0, "amt20": 900.0, "lim": 12, "_stage": {"stage": T.STAGE_BREAK, "level": 9.0}},
            {"symbol": "SZ000012", "concepts": ["B"], "r60": 200.0, "amt20": 800.0, "lim": 10, "_stage": {"stage": T.STAGE_BREAK, "level": 9.0}},
            {"symbol": "SZ000013", "concepts": ["B"], "r60": 100.0, "amt20": 700.0, "lim": 8, "_stage": {"stage": "", "level": None}},
        ]

    def test_topk_per_concept(self):
        picked = T.concept_leaders(self._dom(), topk=2)
        syms = sorted(p["symbol"] for p in picked)
        self.assertEqual(syms, ["SZ000001", "SZ000002", "SZ000011", "SZ000012"])

    def test_small_concept_leader_survives(self):
        picked = {p["symbol"]: p for p in T.concept_leaders(self._dom(), topk=2)}
        self.assertIn("SZ000001", picked, "组内第 1 必须留下，即使它在全局动量最弱")
        self.assertEqual(picked["SZ000001"]["leader_concept"], "A")

    def test_best_concept_wins_for_multi_member(self):
        dom = self._dom()
        dom.append({"symbol": "SZ000001", "concepts": ["B"], "r60": -50.0, "amt20": 1.0, "lim": 0,
                    "_stage": {"stage": T.STAGE_BREAK, "level": 1.0}})
        picked = {p["symbol"]: p for p in T.concept_leaders(dom, topk=2)}
        self.assertEqual(picked["SZ000001"]["leader_concept"], "A", "取**较优**概念（A 组内第 1 优于 B 组末位）")

    def test_topk_zero_is_old_global_mode(self):
        dom = self._dom()
        kept = T.concept_pick(dom, topk=0)
        self.assertEqual(len(kept), len(dom), "topk=0 ⇒ 旧口径：不分组、全量保留")
        self.assertTrue(all("leader" in d for d in kept))

    def test_empty_domain(self):
        self.assertEqual(T.concept_leaders([], topk=2), [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""候选成员排序修复的回归（账本 §9.48/§9.56）。

背景：`stock_confirm_judge` 原来 `SELECT ts_code ... LIMIT 10` **无 ORDER BY** ⇒
取哪 10 只**任意且不可复现**；长飞光纤在「光通信模块」(111 只) 物理序第 46、中国巨石在「PCB」(200 只) 第 37
⇒ 结构上永远进不了候选（91% 主题成分不可见）。
修复（开关 `STOCK_CONFIRM_ORDER`，**库内默认空 = 逐字旧行为**）：
  · `dist20h`   距 20 日收盘高点由近到远（「方向内选强、绝不后排」，§9.53 四个月回放全正）
  · `lead_cond` 大盘 5 日跌（共振回调）⇒ 按 r5 降序（抗跌优先）；否则退化为 dist20h
  · 同分按代码序（确定性）；数据不足/异常 ⇒ 退回代码序（可复现优先）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
os.environ.setdefault("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")

import pandas as pd  # noqa: E402
import stock_confirm_judge as sc  # noqa: E402


def mk(prices):
    """prices: {code: [收盘序列]} → DataFrame（列=代码，索引=日期）。"""
    n = max(len(v) for v in prices.values())
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({c: pd.Series(v + [None] * (n - len(v)), index=idx) for c, v in prices.items()})


class ConfirmOrderTest(unittest.TestCase):
    def test_default_off_keeps_input_order(self):
        close = mk({"A.SH": [10] * 25, "B.SH": [10] * 25})
        self.assertEqual(sc.order_members(["B.SH", "A.SH"], close, mode=""), ["B.SH", "A.SH"],
                         "开关关 ⇒ 完全不干预（逐字旧行为）")

    def test_dist20h_picks_near_high(self):
        # A 在 20 日高附近（新高），B 明显落后（离高点远）⇒ A 应排前
        close = mk({"A.SH": [10 + i for i in range(25)], "B.SH": [20 - i for i in range(25)]})
        out = sc.order_members(["B.SH", "A.SH"], close, mode="dist20h")
        self.assertEqual(out[0], "A.SH", "dist20h 应把「接近 20 日高」的排前面（绝不后排）")
        self.assertEqual(sorted(out), ["A.SH", "B.SH"], "不得增删成员")

    def test_lead_cond_on_pullback_uses_r5(self):
        # 构造「大盘 5 日在跌」，但 C 逆势抗跌（r5 最高）
        down = [100 - i * 0.5 for i in range(25)]          # A 一路阴跌
        close = mk({"A.SH": down, "B.SH": [v - 0.1 for v in down],
                    "C.SH": [100.0] * 20 + [100.5, 101.0, 101.5, 102.0, 103.0]})   # C 横盘后走强（r5 为正）
        out = sc.order_members(["A.SH", "B.SH", "C.SH"], close, mode="lead_cond")
        self.assertEqual(out[0], "C.SH", "共振回调日应按抗跌（r5 高）优先")

    def test_lead_cond_falls_back_when_market_up(self):
        up = [10 + i for i in range(25)]
        close = mk({"A.SH": up, "B.SH": [v * 0.998 for v in up]})
        out = sc.order_members(["A.SH", "B.SH"], close, mode="lead_cond")
        self.assertEqual(out[0], "A.SH", "非回调日 ⇒ 退化为 dist20h")

    def test_deterministic_tiebreak(self):
        close = mk({"B.SH": [10] * 25, "A.SH": [10] * 25})
        self.assertEqual(sc.order_members(["B.SH", "A.SH"], close, mode="dist20h"), ["A.SH", "B.SH"])
        self.assertEqual(sc.order_members(["B.SH", "A.SH"], close, mode="dist20h"), ["A.SH", "B.SH"],
                         "同一天同输入必须同输出（可复现）")

    def test_missing_data_falls_back(self):
        close = mk({"A.SH": [10] * 25})
        out = sc.order_members(["ZZ.SH", "A.SH"], close, mode="dist20h")
        self.assertEqual(sorted(out), ["A.SH", "ZZ.SH"], "缺数据的成员不丢，排到后面")

    def test_wiring_default_off_and_switches(self):
        src = open(os.path.join(ROOT, "apps", "main_line", "stock_confirm_judge.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("STOCK_CONFIRM_ORDER", "")', src, "开关必须默认空")
        self.assertIn("MAX_FETCH", src)
        # 关排序时仍走旧 SQL（LIMIT MAX_STOCKS）
        self.assertIn("(cname, MAX_STOCKS))", src, "关排序时仍走旧的 LIMIT MAX_STOCKS 分支")
        self.assertIn("(cname, MAX_FETCH))", src, "开排序时走全量取数分支")
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("STOCK_CONFIRM_ORDER", pins)
        # 账本 §9.404：新增「人气/弹性优先」口径（pop_lead ✓）
        self.assertIn('if mode == "pop_lead"', src, "pop_lead 分支须存在")
        self.assertIn("vol=vol", src, "调用处须把 vol 传进排序")

    def test_offline_validation_numbers_are_cited(self):
        """把 §9.53 的验证结论写进代码注释/文档，避免"经验证的口径"被后人改掉。"""
        src = open(os.path.join(ROOT, "apps", "main_line", "stock_confirm_judge.py"), encoding="utf-8").read()
        self.assertIn("§9.53", src)
        self.assertIn("绝不后排", src)


if __name__ == "__main__":
    unittest.main()

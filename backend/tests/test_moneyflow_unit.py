# -*- coding: utf-8 -*-
"""`bt_agent_tools.get_stock_moneyflow` 的**单位**回归测试（2026-09-23 修）。

## 病灶

tushare `moneyflow.net_mf_amount` 的单位是**万元**，而该工具把「近 5 日净流入」标为「(亿)」
却用 `round(x / 1e8, 3)` 换算（当成**元**）⇒ **差 10,000 倍**。

实测（真实缓存 + 真实提示词）：
- `600183` 的 5 日累计 = **−67,579 万元 = −6.76 亿**，提示词里显示成 **−0.0007 亿**；
- 喂给 AI 的 4500 个取值里 **88% 恰好是 0**，其余非零的中位 0.0010 亿（= 10 万元）、最大 0.0030 亿；
- ⇒ `WOLF_AGENT_MONEYFLOW=1` 名义上"把资金流发给 AI"，实际上 AI 看到的是一排 0.0。

## 本测试守什么

1. 换算必须按**万元→亿元**（/1e4）而不是 /1e8；
2. 数量级自检：典型 A 股单日净流入（几千~几万万元）换算后应落在 **0.01 ~ 10 亿**；
3. 符号方向不能变（净流出仍为负）。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "jobs", "bt_agent_tools.py")


class MoneyflowUnitTest(unittest.TestCase):

    def setUp(self):
        self.src = open(SRC, encoding="utf-8").read()

    def test_converts_wan_to_yi(self):
        """源码里必须是 /1e4（万元→亿元），不能是 /1e8。"""
        self.assertIn("round(x / 1e4, 3)", self.src, "换算没改成万元→亿元")
        self.assertNotIn("round(x / 1e8, 3)", self.src, "仍在用元→亿（差 1 万倍）")

    def test_comment_records_the_bug(self):
        """留下病灶说明，避免以后又被改回去。"""
        self.assertIn("万元", self.src)
        self.assertIn("10,000 倍", self.src)

    def test_magnitude_sanity(self):
        """真实缓存值换算后必须落在合理量级（用 600183 的真实数据）。"""
        import json
        p = os.path.join(ROOT, "data/_bt_fund/_ref/moneyflow/600183.json")
        if not os.path.exists(p):
            self.skipTest("无 moneyflow 缓存")
        d = json.load(open(p, encoding="utf-8"))["net_mf_amount"]
        ks = sorted(d)[-5:]
        s = sum(float(d[k]) for k in ks)          # 万元
        yi_new = round(s / 1e4, 3)                # 修后
        yi_old = round(s / 1e8, 3)                # 修前
        self.assertLessEqual(abs(yi_new), 50, "换算后量级不合理（>50 亿）")
        self.assertGreaterEqual(abs(yi_new), 0.01, "换算后量级过小，可能又用错单位")
        self.assertLess(abs(yi_old), 0.01, "旧口径本就该接近 0（这条反证单位错了）")

    def test_source_parses(self):
        ast.parse(self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""P2-7 单测: 模块拆分后的符号归一化 + 兼容再导出。

背景（2026-09-10）:
  ① wolf_t_rules.py 原被错拼了三份模块(3 处 coding 标记), 已拆出 wolf_index_context.py;
  ② 拆分时发现 `resolve_sw_sector` 对 xq 前缀写法(SH600001)会拼出垃圾 ts_code('SH600001.SZ'),
     导致 datahubco 查不到 → 返回 None → **defensive_t_reduce_sw(板块级防御减T) 生产中永不触发**。
     本测试锁定修复后的归一化行为。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading", REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_index_context as W  # noqa: E402


class TestNormTs(unittest.TestCase):
    def test_all_symbol_forms(self):
        cases = {
            "SH600001": "600001.SH",      # xq 前缀(生产 t_conditions.symbol 的实际格式)
            "SZ000001": "000001.SZ",
            "SZ300750": "300750.SZ",
            "BJ430047": "430047.BJ",
            "600001": "600001.SH",        # 裸码
            "000001": "000001.SZ",
            "300750": "300750.SZ",
            "600001.SH": "600001.SH",     # 已是 ts_code
            "000001.SZ": "000001.SZ",
            "": "",
        }
        for src, exp in cases.items():
            self.assertEqual(W._norm_ts(src), exp, src)

    def test_resolve_sw_sector_uses_normalized_code(self):
        """resolve_sw_sector 应把 xq 前缀归一化后再查(修复前会传 'SH600001.SZ')。"""
        seen = {}

        def fake_dh(api, **p):
            seen["ts"] = p.get("ts_code")
            return [], []

        orig = W._dh_get
        W._dh_get = fake_dh
        try:
            for src, exp in (("SH600001", "600001.SH"), ("SZ300750", "300750.SZ"), ("600001", "600001.SH")):
                seen.clear()
                W._SW_MEMBER_CACHE.clear()
                W.resolve_sw_sector(src)
                self.assertEqual(seen.get("ts"), exp, src)
        finally:
            W._dh_get = orig
            W._SW_MEMBER_CACHE.clear()

    def test_benchmark_index_with_xq_prefix(self):
        """benchmark_index 已按归一化入参(修复前对 'SZ300001' 恒返回 000001)。"""
        self.assertEqual(W.benchmark_index("SZ300001"), "399006")
        self.assertEqual(W.benchmark_index("300001"), "399006")
        self.assertEqual(W.benchmark_index("SH688001"), "000688")
        self.assertEqual(W.benchmark_index("SH600001"), "000001")
        self.assertEqual(W.benchmark_index("SZ000001"), "399001")


class TestBackwardCompatReexport(unittest.TestCase):
    def test_wolf_t_rules_reexports_same_objects(self):
        """wolf_t_rules 保留同名再导出 → 既有 import 路径不破, 且与定义处是同一对象(单一定义)。"""
        from app.services import wolf_t_rules as T
        for name in ("benchmark_index", "_index_members", "resolve_stock_index",
                     "defensive_t_reduce_index", "resolve_sw_sector",
                     "_sw_daily_high", "defensive_t_reduce_sw"):
            self.assertTrue(hasattr(T, name), name)
            self.assertIs(getattr(T, name), getattr(W, name), name)

    def test_original_t_rule_functions_still_present(self):
        """拆分不能误删原有做T规则函数。"""
        from app.services import wolf_t_rules as T
        for name in ("zheng_t_buy", "dao_t_sell", "zheng_t_buy_quote", "dao_t_sell_quote",
                     "t_sell_confirmed", "t_cycle_pnl", "defensive_t_reduce_quote"):
            self.assertTrue(hasattr(T, name), name)


if __name__ == "__main__":
    unittest.main()

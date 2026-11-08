# -*- coding: utf-8 -*-
"""universe_clean 单测：选股域卫生过滤（退市 / ST / 北交所），as-of 纪律。

关键点：**不能拿今天的名字去过滤历史** —— 实测 6 只宇宙内票里，司尔特/易华录/**闻泰科技**
今天已戴帽(ST/*ST)，但 2026-01-08 当时还没戴帽；用"今天的名字"会把闻泰科技这种大票静默剔掉。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

import universe_clean as U  # noqa: E402


class TestSwitchAndRules(unittest.TestCase):
    def setUp(self):
        for k in ("WOLF_UNIVERSE_CLEAN", "WOLF_UNIVERSE_CLEAN_BJ", "WOLF_UNIVERSE_CLEAN_ST",
                  "WOLF_UNIVERSE_CLEAN_DELIST", "WOLF_UNIVERSE_CLEAN_SNAPSHOT", "WOLF_FUND_TAG"):
            os.environ.pop(k, None)

    def test_default_off(self):
        self.assertFalse(U.enabled(), "库内默认关 ⇒ 生产零影响")
        kept, dropped = U.filter_symbols(["SH600895", "920305.BJ"])
        self.assertEqual(kept, ["SH600895", "920305.BJ"], "开关关 ⇒ 原样返回")
        self.assertEqual(dropped, {})
        self.assertTrue(U.judge("920305.BJ")[0], "开关关 ⇒ 一律放行")

    def test_bj_by_code_rule(self):
        for s in ("920305.BJ", "BJ920015", "430047", "833994", "SH920181", "920139.SH", "830799"):
            self.assertTrue(U.is_bj(s), s)
        for s in ("SH600895", "002371.SZ", "300661.SZ", "688981.SH", "600000.SH"):
            self.assertFalse(U.is_bj(s), s)

    def test_name_rule(self):
        for n in ("ST得润", "*ST闻泰", "云创退", "XST", "退市海润"):
            self.assertTrue(U.name_bad(n)[0], n)
        for n in ("长电科技", "闻泰科技", "易华录", "", None, "司尔特"):
            self.assertFalse(U.name_bad(n)[0], str(n))
        self.assertFalse(U.name_bad("")[0], "空名 ⇒ 不判（fail-open）")

    def test_strict_asof_in_backtest_never_uses_today_snapshot(self):
        """回测里（有 tag）没有 as-of 名称 ⇒ **放行**，绝不用今天的快照判历史。"""
        self.assertFalse(U.use_snapshot())
        os.environ["WOLF_UNIVERSE_CLEAN"] = "1"
        os.environ["WOLF_UNIVERSE_CLEAN_ST"] = "1"
        os.environ["WOLF_FUND_TAG"] = "不存在的tag"      # = 回测上下文，但没有缓存
        try:
            self.assertTrue(U.judge("002538.SZ", day="20260108")[0],
                            "有 tag(回测) + 无 as-of 名称 ⇒ 放行（宁漏勿穿越未来）")
        finally:
            self.setUp()

    def test_live_mode_uses_current_snapshot(self):
        """**不在回测里**（无 tag）⇒ 快照就是"当下真实名称"，生产口径正确、不是穿越未来。"""
        os.environ["WOLF_UNIVERSE_CLEAN"] = "1"
        os.environ["WOLF_UNIVERSE_CLEAN_ST"] = "1"
        os.environ.pop("WOLF_FUND_TAG", None)
        os.environ.pop("SZ_TAG", None)
        try:
            ok, why = U.judge("002538.SZ")       # 今天叫 ST司特
            self.assertFalse(ok, "生产口径：当前名称含 ST ⇒ 拦")
            self.assertIn("ST", why)
        finally:
            self.setUp()

    def test_bj_still_filtered_in_strict_mode(self):
        """北交所靠代码规则判，与是否取到 as-of 名称无关（确定性、无未来函数）。"""
        os.environ["WOLF_UNIVERSE_CLEAN"] = "1"
        try:
            self.assertFalse(U.judge("920305.BJ", day="20260108")[0])
            self.assertTrue(U.judge("SH600895", day="20260108")[0])
        finally:
            self.setUp()

    def test_drop_reasons_counted(self):
        os.environ["WOLF_UNIVERSE_CLEAN"] = "1"
        try:
            kept, dropped = U.filter_symbols(["SH600895", "920305.BJ", "BJ920015", "002371.SZ"],
                                             day="20260108")
            self.assertEqual(len(kept), 2)
            self.assertEqual(dropped.get("北交所"), 2)
        finally:
            self.setUp()


class TestAsOfNames(unittest.TestCase):
    """有缓存时按 as-of 名称判（不依赖网络：直接构造缓存文件）。"""

    def setUp(self):
        import tempfile, json, shutil
        self.tmp = tempfile.mkdtemp(prefix="uc_")
        sys.path.insert(0, os.path.join(ROOT, "jobs"))
        import bt_fund_asof as F
        self.F = F
        self._root = F.ROOT
        F.ROOT = self.tmp
        d = os.path.join(self.tmp, "_ref", "namechange")   # 曾用名是参考数据，固定落 _ref
        os.makedirs(d)
        # 司尔特：2026-01-08 叫「司尔特」，2026-02-10 起叫「ST司特」
        json.dump([{"name": "司尔特", "start_date": "20110101", "end_date": "20260209"},
                   {"name": "ST司特", "start_date": "20260210", "end_date": None}],
                  open(os.path.join(d, "002538.SZ.json"), "w"))
        os.environ["WOLF_UNIVERSE_CLEAN"] = "1"
        os.environ["WOLF_FUND_TAG"] = "T"

    def tearDown(self):
        import shutil
        self.F.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k in ("WOLF_UNIVERSE_CLEAN", "WOLF_FUND_TAG"):
            os.environ.pop(k, None)

    def test_asof_name_not_today_name(self):
        self.assertEqual(self.F.name_as_of("002538.SZ", "20260108", "T"), "司尔特")
        self.assertEqual(self.F.name_as_of("002538.SZ", "20260301", "T"), "ST司特")
        self.assertTrue(U.judge("002538.SZ", day="20260108")[0], "1/8 还没戴帽 ⇒ 放行")
        self.assertFalse(U.judge("002538.SZ", day="20260301")[0], "3/1 已戴帽 ⇒ 剔除")
        self.assertIn("ST", U.judge("002538.SZ", day="20260301")[1])


if __name__ == "__main__":
    unittest.main()

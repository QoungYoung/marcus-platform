# -*- coding: utf-8 -*-
"""L3 v1 回归：**建仓腿下单下限 = min(底仓缺口/步长, 可用现金×0.98)**（`WOLF_BUILD_SIZE_FLOOR`）。

病灶（账本 §9.33）：建仓腿的实付量由 **AI 建议**决定（0401 SZ002294 只买 300 股 = 7.7% 权益），
而 `WOLF_BUILD_CAP_ANCHOR` 只抬**上限** ⇒ 缺口/步长（≈16%）几乎从未用满 ⇒ 仓位长期 17~25%（他 ≥65%）。
语料 2026-06-15「突破下跌趋势第一根开头我加一半 突破后的第二根红K尾盘 打满」。
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")

import app.services.t_gateway as gw  # noqa: E402


class BuildSizeFloorTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("WOLF_BUILD_SIZE_FLOOR")
        os.environ.pop("WOLF_BUILD_SIZE_FLOOR", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("WOLF_BUILD_SIZE_FLOOR", None)
        else:
            os.environ["WOLF_BUILD_SIZE_FLOOR"] = self._old

    def test_default_off(self):
        self.assertFalse(gw._BUILD_SIZE_FLOOR)
        v, why = gw.build_size_floor("SH600584", 40.0, 300, trigger_id=None, condition_id=None)
        self.assertEqual(v, 300)
        self.assertIn("未开", why)

    def test_lifts_to_anchor(self):
        """开关开 + 建仓类腿型：请求 300 股（1.2 万）⇒ 抬到缺口/步长（≈16% 权益 = 4 万 ⇒ 1000 股）。"""
        os.environ["WOLF_BUILD_SIZE_FLOOR"] = "1"
        gw._BUILD_SIZE_FLOOR = True  # 常量在 import 期求值，测试内直接翻
        try:
            with mock.patch.object(gw, "_turnover_kind", return_value="trend_break_buy"), \
                 mock.patch.object(gw, "get_sellable_ledger", return_value={}), \
                 mock.patch.object(gw, "_account_available_cash", return_value=200000.0), \
                 mock.patch("app.services.t_capacity.account_equity", return_value=250000.0), \
                 mock.patch("app.services.t_capacity.clamp_buy_volume",
                            return_value=(40000 // 40 // 100 * 100, "缺口/3")):
                v, why = gw.build_size_floor("SH600584", 40.0, 300, trigger_id=1, condition_id=None)
            self.assertEqual(v, 1000, "应抬到锚（1000 股）")
            self.assertIn("L3 建仓下限", why)
        finally:
            gw._BUILD_SIZE_FLOOR = False

    def test_no_press_down(self):
        """锚小于请求量 ⇒ 不压量（只抬不压）。"""
        os.environ["WOLF_BUILD_SIZE_FLOOR"] = "1"
        gw._BUILD_SIZE_FLOOR = True
        try:
            with mock.patch.object(gw, "_turnover_kind", return_value="trend_break_buy"), \
                 mock.patch.object(gw, "get_sellable_ledger", return_value={}), \
                 mock.patch.object(gw, "_account_available_cash", return_value=1e9), \
                 mock.patch("app.services.t_capacity.account_equity", return_value=250000.0), \
                 mock.patch("app.services.t_capacity.clamp_buy_volume", return_value=(200, "小锚")):
                v, _why = gw.build_size_floor("SH600584", 40.0, 1000, trigger_id=1, condition_id=None)
            self.assertEqual(v, 1000, "不得压量")
        finally:
            gw._BUILD_SIZE_FLOOR = False

    def test_non_build_kind_untouched(self):
        os.environ["WOLF_BUILD_SIZE_FLOOR"] = "1"
        gw._BUILD_SIZE_FLOOR = True
        try:
            with mock.patch.object(gw, "_turnover_kind", return_value="wolf_zheng_t_buy"):
                v, why = gw.build_size_floor("SH600584", 40.0, 300, trigger_id=1, condition_id=None)
            self.assertEqual(v, 300)
            self.assertIn("非建仓类腿型", why)
        finally:
            gw._BUILD_SIZE_FLOOR = False

    def test_cash_caps_anchor(self):
        """可用现金不足 ⇒ 锚被现金压小（不放大到买不起的量）。"""
        os.environ["WOLF_BUILD_SIZE_FLOOR"] = "1"
        gw._BUILD_SIZE_FLOOR = True
        try:
            with mock.patch.object(gw, "_turnover_kind", return_value="trend_break_buy"), \
                 mock.patch.object(gw, "get_sellable_ledger", return_value={}), \
                 mock.patch.object(gw, "_account_available_cash", return_value=40000.0), \
                 mock.patch("app.services.t_capacity.account_equity", return_value=250000.0), \
                 mock.patch("app.services.t_capacity.clamp_buy_volume", return_value=(100000, "大锚")):
                v, _why = gw.build_size_floor("SH600584", 40.0, 300, trigger_id=1, condition_id=None)
            self.assertEqual(v, 900, "40000×0.98/40 → 900 股（整手）")
        finally:
            gw._BUILD_SIZE_FLOOR = False

    def test_wiring_and_pins(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("def build_size_floor(", src)
        self.assertIn("_v_floor, _why_floor = build_size_floor(", src)
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_BUILD_SIZE_FLOOR", pins)


if __name__ == "__main__":
    unittest.main()

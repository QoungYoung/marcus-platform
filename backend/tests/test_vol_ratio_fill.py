# -*- coding: utf-8 -*-
"""形态腿量比补齐（`WOLF_VOL_RATIO_FILL`）回归测试 —— 2026-09-24。

## 病灶（账本 §9.13）

prompt 的【决策 checklist】第 ④ 条要求判「恐慌放量追跌（**量比骤升**+创新低）」，
但 ① **as-of 数据块里没有量比**（只有换手率/成交量/成交额）② **放量破位门**
（`wolf_dip_volume_gate`：放量破前低 ⇒ 买腿不执行）对**形态腿**恒 fail-open ——
因为它从 `quote.vol_ratio` 取数，而**回测取数替身不提供该字段**（实测四臂 0 次真实拦截）。
⇒ AI 只能从散文里猜量能 ⇒ 那类判断抛硬币（13 个抛硬币节点里 9 个是它）。

## 本测试守什么

1. **开关关 ⇒ 逐位旧行为**（只回 quote 自带值，缺则 None）；
2. 开 ⇒ quote 有值就**用 quote 的**（不覆盖）；
3. 开 + quote 无值 ⇒ 用**与条件腿同一算法**按 `turnover_rate` 补算（给出可算的期望值）；
4. 开 ⇒ **基准用该股自己的**（`symbol_turnover_base` 取到什么就用什么，不是写死 0.5）；
5. 取不到基准 ⇒ 退回 `MIN_TURNOVER_BASE`（而不是异常/None）；
6. 开盘前/无换手/异常 ⇒ **fail-open 返回 None**；
7. 静态接线：`_insert_wolf_trigger` 走 `wolf_leg_vol_ratio`；as-of 块调 `_vr_line`；
   库内默认 `"0"`；pins 与驱动在回测置 1。
"""
import ast
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import t_monitor as tm  # noqa: E402

Q = {"turnover_rate": 1.0, "current": 10.0}
NOW = dt.datetime(2026, 3, 23, 10, 30)          # opened = 60 分钟 ⇒ 240/60 = 4


def _vr(quote, base=None, on=True, sym="SZ000001", now=NOW):
    with mock.patch.object(tm, "VOL_RATIO_FILL", on), \
         mock.patch.object(tm, "symbol_turnover_base", return_value=base):
        return tm.wolf_leg_vol_ratio(quote, symbol=sym, now=now)


class VolRatioFillTest(unittest.TestCase):

    def test_default_off_is_legacy(self):
        """开关关 ⇒ 只回 quote 自带值；缺则 None（旧行为）。"""
        self.assertIsNone(_vr({}, on=False))
        self.assertEqual(_vr({"vol_ratio": 1.23}, on=False), 1.23)
        self.assertEqual(_vr({"vr": 0.9}, on=False), 0.9)

    def test_on_prefers_quote_value(self):
        """开 ⇒ quote 自带值优先（不覆盖已有数据）。"""
        self.assertEqual(_vr({"vol_ratio": 2.5, "turnover_rate": 99}, on=True), 2.5)

    def test_on_computes_from_turnover_with_symbol_base(self):
        """开 + 无 vol_ratio ⇒ 用条件腿同算法补算：turnover×(240/opened)/base。"""
        # turnover=1.0, opened=60 ⇒ scaled=4.0；base=2.0% ⇒ 2.0
        self.assertAlmostEqual(_vr(Q, base=2.0), 2.0, places=3)
        # base 是该股自己的 ⇒ 换基准结果就变（证明不是写死 0.5）
        self.assertAlmostEqual(_vr(Q, base=1.0), 4.0, places=3)
        self.assertAlmostEqual(_vr(Q, base=8.0), 0.5, places=3)

    def test_on_falls_back_to_min_base(self):
        """取不到该股基准 ⇒ 退回 MIN_TURNOVER_BASE（不异常、不返回 None）。"""
        got = _vr(Q, base=None)
        self.assertAlmostEqual(got, round(4.0 / tm.MIN_TURNOVER_BASE, 3), places=3)

    def test_fail_open(self):
        """开盘前 / 无换手 / 异常 ⇒ None（fail-open，绝不因缺数误拦）。"""
        self.assertIsNone(_vr({"turnover_rate": 0}, base=2.0))
        self.assertIsNone(_vr({}, base=2.0))
        self.assertIsNone(_vr(Q, base=2.0, now=dt.datetime(2026, 3, 23, 9, 10)))   # 开盘前
        with mock.patch.object(tm, "VOL_RATIO_FILL", True), \
             mock.patch.object(tm, "symbol_turnover_base", side_effect=RuntimeError("boom")):
            self.assertIsNone(tm.wolf_leg_vol_ratio(Q, symbol="X", now=NOW))


class WiringTest(unittest.TestCase):

    def _src(self, rel):
        return open(os.path.join(ROOT, rel), encoding="utf-8").read()

    def test_wolf_trigger_uses_helper(self):
        src = self._src("backend/app/services/t_monitor.py")
        self.assertIn("_vr = wolf_leg_vol_ratio(quote, symbol=sym)", src)

    def test_asof_block_renders_vol_ratio(self):
        src = self._src("jobs/bt_agent_tools.py")
        self.assertIn("_vr_line(symbol, q)", src)
        self.assertIn("量比: %s", src)

    def test_defaults_and_pins(self):
        self.assertIn('os.getenv("WOLF_VOL_RATIO_FILL", "0")', self._src("backend/app/services/t_monitor.py"))
        self.assertIn("WOLF_VOL_RATIO_FILL='1'", self._src("jobs/bt_env_pins.sh"))
        self.assertIn('("WOLF_VOL_RATIO_FILL", "1")', self._src("jobs/bt_prod_run.py"))

    def test_base_comes_from_turnover_profile(self):
        src = self._src("backend/app/services/t_monitor.py")
        tree = ast.parse(src)
        ok = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "symbol_turnover_base":
                seg = ast.get_source_segment(src, node) or ""
                ok = "compute_turnover_profile" in seg and "same_minute_avg" in seg
        self.assertTrue(ok, "基准必须取自 t_turnover_profile 的 same_minute_avg")


if __name__ == "__main__":
    unittest.main(verbosity=2)

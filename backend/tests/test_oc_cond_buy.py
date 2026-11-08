# -*- coding: utf-8 -*-
"""开盘不追高闸·**条件单补线**（`WOLF_OC_COND_BUY`）回归测试 —— 2026-09-23。

## 病灶（实测 T5 全窗）

「开盘快速拉升不追」闸（`wolf_no_chase.open_verdict`，狼大 2025-04-15 条件6 /
2026-01-12「千万绝对不要开盘买」）装在 `t_gateway.gateway_execute`，
但它的守卫是 `if _nco.open_enabled() and side == "buy" and trigger_id:` ——
**而条件单成交那条路径只传 `condition_id`、不传 `trigger_id`**
（`t_monitor._round` 的 `gateway_execute(symbol, side, current, volume, ...,
condition_id=cond.get("id"), ...)`）⇒ **条件单买入完全漏过该闸**（不追高闸同理）。

证据链（`data/_bt_t5/_summary/prod_*.log` + PG 成交）：
- 闸门本身是活的：20260414 当天拦下了 `SZ002463`（+3.52%）、`SZ002008`（+5.12%）——
  但那是**AI 腿**（走带 trigger_id 的另一条路）；
- 同日 `SH603061` 的**条件单**（reason=`条件命中自动执行（trend_break_buy）`）
  在 09:40 买 323.30，当日开盘 308.0 ⇒ **+4.97% ≥ 阈值 3.0%，本该被拦却成交**；
- 该腿后来是全臂最大亏损腿（−8,455，实为除权假象；真实仍为最大亏损之一）；
- 同型：`SZ001309` 20260318 买 361.50、`SZ002294` 20260401 买 63.36/64.20 ——
  reason 全是 `条件命中自动执行（trend_break_buy）`。

## 修法（外科式补线，单变量）

在条件单成交前直接调 `wolf_no_chase.open_verdict`（用当日 `quote.open`），
**只补开盘闸**，不动换手闸/不追高闸；开关 `WOLF_OC_COND_BUY` **库内默认 0**（生产逐位不变），
回测由 `jobs/bt_env_pins.sh` + `bt_prod_run.py` setdefault 置 1。

## 本测试守什么

1. 开关关 ⇒ 逐位旧行为（不拦任何条件单买入）；
2. 开关开 ⇒ 复现 SH603061 那笔（+4.97%）**被拦**；
3. 开 ⇒ 同日第一笔（+2.36%）**放行**（阈值语义正确，不误拦）；
4. 缺 `open` / 异常 / 卖腿 ⇒ **fail-open**（绝不误拦）；
5. 接线顺序：`_oc_cond_buy_block` 必须在同函数内 `gateway_execute` **之前**调用；
6. pins 与驱动兜底同值（回测默认开），且库内默认值是 `"0"`。
"""
import ast
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import t_monitor as tm            # noqa: E402
from app.services import wolf_no_chase as nc        # noqa: E402


def _block(px, open_px, kind="trend_break_buy", on=True, open_on=True, hhmm="0940"):
    """默认把时刻钉在 09:40（病灶时刻；闸门只在 0930–0945 生效）。"""
    with mock.patch.object(tm, "OC_COND_BUY", on), \
         mock.patch.object(nc, "OPEN_ON", open_on):
        return tm._oc_cond_buy_block(px, {"open": open_px} if open_px else {}, kind, hhmm=hhmm)


class OcCondBuyTest(unittest.TestCase):

    def test_switch_default_off_is_passthrough(self):
        """开关关 ⇒ 一律放行（生产逐位不变）。"""
        b, why = _block(323.30, 308.0, on=False)
        self.assertFalse(b)
        self.assertEqual(why, "")

    def test_repro_sh603061_second_lot_is_blocked(self):
        """复现病灶：09:40 买 323.30 / 当日开盘 308.0 = +4.97% ≥ 3% ⇒ 拦。"""
        b, why = _block(323.30, 308.0)
        self.assertTrue(b, "本该被拦（+4.97%% ≥ 3.0%%），实际放行 ⇒ bug 未修")
        self.assertIn("开盘", why)

    def test_first_lot_still_passes(self):
        """同日第一笔 315.26 / 308.0 = +2.36% < 3% ⇒ 不误拦。"""
        b, _ = _block(315.26, 308.0)
        self.assertFalse(b)

    def test_threshold_is_strict_at_3pct(self):
        """恰好在阈值上（+3.00%）⇒ 拦（`prem < RISE_PCT` 才放行）。"""
        b, _ = _block(308.0 * 1.03, 308.0)
        self.assertTrue(b)
        b2, _ = _block(308.0 * 1.0299, 308.0)
        self.assertFalse(b2)

    def test_fail_open(self):
        """缺 open / 缺现价 / 闸门全局关 ⇒ 放行（绝不误拦）。"""
        for px, op, kw in ((323.30, 0, {}), (0, 308.0, {}),
                           (323.30, 308.0, {"open_on": False}),
                           (323.30, 308.0, {"on": False}),
                           (323.30, 308.0, {"hhmm": "1030"})):
            b, why = _block(px, op, **kw)
            self.assertFalse(b, "应 fail-open：px=%s open=%s %s" % (px, op, kw))
            self.assertEqual(why, "")

    def test_sell_side_not_applicable(self):
        """卖腿不受该闸约束（open_verdict 直接判放行）。"""
        with mock.patch.object(nc, "OPEN_ON", True):
            ok, why = nc.open_verdict(323.30, 308.0, hhmm="0940", side="sell",
                                      kind="custom_support_sell")
        self.assertTrue(ok)

    def test_non_open_window_passes(self):
        """不在 0930–0945 窗口 ⇒ 放行（该闸只管开盘段）。"""
        with mock.patch.object(nc, "OPEN_ON", True):
            ok, _ = nc.open_verdict(323.30, 308.0, hhmm="1030", side="buy",
                                    kind="trend_break_buy")
        self.assertTrue(ok)


class WiringTest(unittest.TestCase):
    """静态守住接线（不依赖运行环境）。"""

    def _src(self):
        p = os.path.join(ROOT, "backend", "app", "services", "t_monitor.py")
        return open(p, encoding="utf-8").read()

    def test_helper_called_before_gateway_in_same_func(self):
        src = self._src()
        tree = ast.parse(src)
        hit = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    nm = getattr(f, "id", None) or getattr(f, "attr", None)
                    if nm in ("_oc_cond_buy_block", "gateway_execute"):
                        calls.append((sub.lineno, nm))
            calls.sort()
            names = [n for _, n in calls]
            if "_oc_cond_buy_block" in names and "gateway_execute" in names:
                i = names.index("_oc_cond_buy_block")
                j = names.index("gateway_execute")
                self.assertLess(i, j, "补线必须在 gateway_execute 之前判定")
                hit = True
        self.assertTrue(hit, "未找到同函数内 _oc_cond_buy_block 与 gateway_execute 的接线")

    def test_library_default_is_off(self):
        src = self._src()
        self.assertIn('os.getenv("WOLF_OC_COND_BUY", "0")', src)

    def test_pins_and_driver_turn_it_on_for_backtest(self):
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_OC_COND_BUY='1'", pins)
        drv = open(os.path.join(ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        self.assertIn('("WOLF_OC_COND_BUY", "1")', drv)


if __name__ == "__main__":
    unittest.main(verbosity=2)

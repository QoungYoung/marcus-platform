# -*- coding: utf-8 -*-
"""尾盘未确认必T出（`WOLF_DAY_END_T_OUT`）回归测试 —— 2026-09-25。

## 病灶（账本 §9.17/§9.18）

狼大对做T的日结纪律是**反复、逐字**的：
2025-06-04「盘中做T尾盘必须出场，**不加仓隔夜**，确保安全」；
2025-08-14「一直到**2点附近不管有没有涨幅我都T完**」；
2026-03-05「做T当日出来，**不留过夜**」；2026-03-17「做T的仓位**当日出来**」；
2026-03-31「做T的当日都会出来，不留过夜」；2026-08-12「T+0 的仓…平时不留仓位」。

而我们把这条**降级**了：`t_monitor._round()` 里只留一句注释
「day_end 已降级: 不做'未确认→必卖'(那批几乎全亏)」，`_check_day_end_de_t()`
**定义了却没有任何调用点**（全仓 grep 无调用）。

## 本测试守什么

1. 库内默认**关**（`WOLF_DAY_END_T_OUT` 未设 ⇒ False）——生产逐位旧行为；
2. 开 ⇒ `_round()` 的调用点会调 `_check_day_end_de_t()`（静态接线）；
3. `_check_day_end_de_t()` 的时间门：**14:45 之前不动作**，14:45 之后对「当日买过、今日未卖、
   未写过该腿」的标的写一条 `wolf_day_end_de_t` 触发；
4. 已写过（`_wolf_done`）/ 当日已卖（`_wolf_sold_today`）/ 无行情 ⇒ **不重复、不误发**；
5. 静态接线：pins 里开关存在且为 '0'（本轮不上，理由是 A股 T+1 下无底仓腿当日卖不掉），
   `WOLF_T_BUY_REQUIRE_BASE='1'`（T15 的唯一变量）；
6. T15 臂脚本 = T14 + 该唯一变量（无底仓闸 1、日结闸 0）。
"""
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import t_monitor as tm  # noqa: E402


def _fake_dt(hh, mm):
    """替换 t_monitor.datetime：now() 返回钉在 hh:mm 的实例，strftime('%Y%m%d') 返回 20260925。"""

    class _DT:
        hour, minute = hh, mm

        @classmethod
        def now(cls):
            return cls()

        def strftime(self, fmt):
            return "20260925" if fmt == "%Y%m%d" else ""

    return _DT


class _Stub:
    def __init__(self, sym="SZ000021", hh=14, mm=50):
        self._sym, self._hh, self._mm = sym, hh, mm
        self._wolf_bought_today = {(sym, "20260925")}
        self._wolf_sold_today = set()
        self._wolf_done = set()
        self._status = {"errors": 0}
        self.written = []

    def _insert_wolf_trigger(self, sym, kind, q, reason):
        self.written.append((sym, kind, reason))


def _call(stub):
    fake = _fake_dt(stub._hh, stub._mm)
    with mock.patch.object(tm, "datetime", fake), \
         mock.patch.object(tm, "fetch_tencent_quote",
                           lambda syms, timeout=8: {syms[0]: {"current": 10.0, "open": 9.9}}), \
         mock.patch.object(tm, "_normalize_symbol", lambda s: s):
        tm.TMonitor._check_day_end_de_t(stub)


class DayEndTOutTest(unittest.TestCase):
    # ── ① 库内默认关 ────────────────────────────────────────────────────────
    def test_default_off(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_monitor.py"),
                   encoding="utf-8").read()
        self.assertIn('DAY_END_T_OUT = os.getenv("WOLF_DAY_END_T_OUT", "0")', src)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WOLF_DAY_END_T_OUT", None)
            self.assertFalse(tm.DAY_END_T_OUT)

    # ── ② 调用点接线（开关门）──────────────────────────────────────────────
    def test_wired_under_switch(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_monitor.py"),
                   encoding="utf-8").read()
        m = re.search(r"if DAY_END_T_OUT:\s*\n\s*self\._check_day_end_de_t\(\)", src)
        self.assertIsNotNone(m, "调用点必须由 DAY_END_T_OUT 开关守着（默认关⇒旧行为）")
        self.assertIn("day_end 默认降级", src)

    # ── ③ 时间门：14:45 之前不动作 ─────────────────────────────────────────
    def test_before_1445_no_action(self):
        for hh, mm in ((9, 45), (11, 30), (14, 0), (14, 44)):
            s = _Stub(hh=hh, mm=mm)
            _call(s)
            self.assertEqual(s.written, [], "%02d:%02d 不应动作" % (hh, mm))

    # ── ④ 14:45 之后写腿（且只写一次）──────────────────────────────────────
    def test_after_1445_writes_once(self):
        s = _Stub(hh=14, mm=45)
        _call(s)
        self.assertEqual(len(s.written), 1)
        sym, kind, reason = s.written[0]
        self.assertEqual((sym, kind), ("SZ000021", "wolf_day_end_de_t"))
        self.assertIn("未确认", reason)
        s.written.clear()
        _call(s)                                   # _wolf_done 已记 ⇒ 不重复
        self.assertEqual(s.written, [])

    def test_skips_already_sold_or_missing_quote(self):
        s = _Stub(hh=14, mm=50)
        s._wolf_sold_today = {("SZ000021", "20260925")}
        _call(s)
        self.assertEqual(s.written, [], "当日已T出 ⇒ 不该再写")
        s2 = _Stub(hh=15, mm=0)
        fake = _fake_dt(15, 0)
        with mock.patch.object(tm, "datetime", fake), \
             mock.patch.object(tm, "fetch_tencent_quote", lambda syms, timeout=8: {}), \
             mock.patch.object(tm, "_normalize_symbol", lambda s: s):
            tm.TMonitor._check_day_end_de_t(s2)
        self.assertEqual(s2.written, [], "无行情 ⇒ 跳过")

    # ── ⑤ pins / 臂脚本接线 ────────────────────────────────────────────────
    def test_pins_and_arm_script(self):
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("export WOLF_DAY_END_T_OUT='0'", pins)
        self.assertIn("export WOLF_T_BUY_REQUIRE_BASE='1'", pins)
        self.assertNotIn("export WOLF_T_BUY_REQUIRE_BASE='0'", pins)
        arm = open(os.path.join(ROOT, ".dsh-tmp", "wolfbt", "_run_t15_exec.sh"),
                   encoding="utf-8").read()
        self.assertIn("export WOLF_T_BUY_REQUIRE_BASE=1", arm)
        self.assertIn("export WOLF_DAY_END_T_OUT=0", arm)
        self.assertIn("TAG=t15", arm)
        for inv in ("export WOLF_ADJ_PRICE=1", "export WOLF_OC_COND_BUY=1"):
            self.assertIn(inv, arm, "T15 必须继承 T14 的不变量 %s" % inv)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""底仓锚「浮亏限定版」`WOLF_BASE_FLOOR_LOSS_EXEMPT`（账本 §9.110/§9.111，用户 2026-09-25 拍板）。

机制（`wolf_exit_rules.floor_exempt`）：
  · **盈利票一律不豁免** ⇒ 底仓锚照旧（保护"挣大钱的票"；通富微电 +27.6% 那笔就不该被卖飞 ✓）
  · **浮亏 ≤ WOLF_FLOOR_LOSS_PCT（默认 −3%）** ⇒ 底仓锚归零 ⇒ 允许卖底仓（止损不再被底仓挡 ✓）
量化依据：一刀切豁免会亏 1.27 万 ✗；本"浮亏限定"只放开 2 个仓位、合计**少亏 2,681 元** ✓
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))


def _reload(**env):
    import app.services.wolf_exit_rules as wr
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(wr)


def _stub(wr, pnl, ma5_break=False):
    """把 evaluate 换成固定结果（纯逻辑测试，不碰库）。"""
    wr.evaluate = lambda account_id, symbol, cur_price=0.0: {"pnl": pnl, "ma5_break": ma5_break}


def test_off_by_default():
    wr = _reload(WOLF_BASE_FLOOR_LOSS_EXEMPT=None, WOLF_MA5_EXIT=None)
    assert wr.FLOOR_LOSS_ON is False and wr.MA5_EXIT_ON is False
    _stub(wr, -50.0)
    assert wr.floor_exempt("a", "SZ000001", 1.0) is False      # 两个开关都关 ⇒ 恒不豁免


def test_profit_never_exempt():
    """盈利票一律不豁免（这是"浮盈回踩不卖"的保护来源 ✓）。"""
    wr = _reload(WOLF_BASE_FLOOR_LOSS_EXEMPT="1", WOLF_MA5_EXIT=None)
    _stub(wr, +27.6)
    assert wr.floor_exempt("a", "SZ002156", 1.0) is False


def test_loss_beyond_threshold_exempt():
    wr = _reload(WOLF_BASE_FLOOR_LOSS_EXEMPT="1", WOLF_FLOOR_LOSS_PCT="-3", WOLF_MA5_EXIT=None)
    _stub(wr, -11.93)
    assert wr.floor_exempt("a", "SH601069", 1.0) is True       # 西部黄金那笔（−11.9%）✓
    _stub(wr, -3.10)
    assert wr.floor_exempt("a", "SZ002074", 1.0) is True       # −3.1% 那笔 ✓
    _stub(wr, -1.00)
    assert wr.floor_exempt("a", "SZ000001", 1.0) is False      # 浮亏未超阈值 ⇒ 不豁免


def test_driver_and_pins_enabled():
    """驱动与 pins 都必须打开（且驱动里不能再留着"非语料⇒0"的旧口径）。"""
    drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_BASE_FLOOR_LOSS_EXEMPT", "1")' in drv
    assert '("WOLF_BASE_FLOOR_LOSS_EXEMPT", "0")' not in drv
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_BASE_FLOOR_LOSS_EXEMPT='1'" in pins

# -*- coding: utf-8 -*-
"""底仓穿透「收紧版」（`WOLF_BASE_PENETRATE_STRICT`）——账本 §9.129，用户 2026-09-25「单独上 (b)」。

量化依据：全臂 **63 次底仓穿透全部来自 struct_weak（趋势转弱）** ✗，而「破位/减仓语义」关键词路径
**一次都没命中** ✗；穿透之后 +5 日中位 +1.47%（为正 **67%**）、+10 日中位 +2.24%（为正 **78%**）✗✗
⇒ 「趋势转弱就卖底仓」**多数卖在反弹前** ⇒ 收紧 ✓（典型 0211 SZ002156 四臂重演，卖后 +9.42%）
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))


def _reload(**env):
    import app.services.t_capacity as tc
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(tc)


def test_default_keeps_old_behavior():
    tc = _reload(WOLF_BASE_PENETRATE_STRICT=None, WOLF_SELL_BASE_EXEMPT="1")
    assert tc.BASE_PENETRATE_STRICT is False
    # 旧行为：趋势转弱 ⇒ 穿透 ✓
    assert tc.base_penetrate_allowed("custom_support_sell", "", struct_weak=True) is True


def test_strict_ignores_struct_weak():
    tc = _reload(WOLF_BASE_PENETRATE_STRICT="1", WOLF_SELL_BASE_EXEMPT="1")
    assert tc.BASE_PENETRATE_STRICT is True
    # 收紧后：仅"趋势转弱"不给穿透 ✗（该腿卖不出底仓 ⇒ 量推导为 0 ⇒ 保住底仓 ✓）
    assert tc.base_penetrate_allowed("custom_support_sell", "", struct_weak=True) is False


def test_strict_keeps_corpus_semantics():
    """语料语义路径不受影响：reason 含「破位」/「减仓」仍然穿透 ✓（含 kind 白名单 ✓）。"""
    tc = _reload(WOLF_BASE_PENETRATE_STRICT="1", WOLF_SELL_BASE_EXEMPT="1")
    assert tc.base_penetrate_allowed("custom_support_sell", "破位卖出", struct_weak=True) is True
    assert tc.base_penetrate_allowed("custom_support_sell", "减仓到 40%", struct_weak=False) is True
    assert tc.base_penetrate_allowed("wolf_defensive_t_reduce", "", struct_weak=False) is True


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_BASE_PENETRATE_STRICT='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_BASE_PENETRATE_STRICT", "0")' in lad

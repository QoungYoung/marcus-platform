# -*- coding: utf-8 -*-
"""低吸「摊薄」前提门（`WOLF_LOWDIP_ON_TREND_ONLY`）——账本 §9.139，用户 2026-09-25「试试吧」。

语料：低吸 = **建仓/加仓摊薄**（618 建仓 / 786 补 / 小跌小买 / 补仓点隔远 ✓）
数据：实测 **40 笔低吸全部**发生在「票上**没有**趋势底仓」的票上 ✗ ⇒ 摊薄实为「下跌中加仓」✗
      ⇒ 历史 **0 样本** ⇒ 无法回测，只能**前瞻验证** ✓
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))


def test_switch_default_off_and_literal_read():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_LOWDIP_ON_TREND_ONLY", "0")' in src   # 库内默认关 ✓
    i = src.index("_lowdip_trend_ok")
    assert "direction LIKE" in src[i:i + 1600] and "trend_break" in src[i:i + 1600]
    assert "return True" in src[i:i + 1600]                        # 取数失败 ⇒ fail-open ✓


def test_gate_is_after_budget_and_blocks_before_return():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    i_budget = src.index("低吸额度：")
    i_gate = src.index("低吸趋势票门拦住")
    assert i_budget < i_gate, "新门应在低吸额度之后（额度→趋势票→…）"
    assert 'is_lowdip_kind(_k_to)' in src
    assert '"level": "LOWDIP_TREND_ONLY"' in src


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_LOWDIP_ON_TREND_ONLY='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_LOWDIP_ON_TREND_ONLY", "0")' in lad

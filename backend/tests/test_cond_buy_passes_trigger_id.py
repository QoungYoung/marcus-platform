# -*- coding: utf-8 -*-
"""条件单自动执行口**必须传 `trigger_id`**（否则网关两台低吸闸门被跳过 ✗）——账本 §9.160。

实测（T35 一月）：`低吸额度：` 0 行、`低吸趋势票门拦住` 0 行 ✗，
而低吸腿仍成交 **11 笔、亏 −6,700** ✗ ⇒ 直接造成"一月依赖通富微电"（+2,870 ✓）。
"""
from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_auto_exec_passes_trigger_id():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = src.index('reason=f"条件命中自动执行（{trigger_kind}）"')
    seg = src[i - 300:i + 500]
    assert "trigger_id=trig_id" in seg, "自动执行口必须传 trigger_id ✗"
    assert 'condition_id=cond.get("id")' in seg


def test_gateway_signature_accepts_trigger_id():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    i = src.index("def gateway_execute(")
    assert "trigger_id" in src[i:i + 400]
    # 两台低吸闸门都依赖 trigger_id 非空 ✓
    j = src.index("WOLF_LOWDIP_ON_TREND_ONLY")
    assert "trigger_id" in src[j:j + 900]

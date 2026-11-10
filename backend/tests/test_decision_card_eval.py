# -*- coding: utf-8 -*-
"""决策卡评分（错过/错做/做小）纯函数用例。"""
import importlib.util
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[2] / "jobs" / "eval_decision_cards.py"
_spec = importlib.util.spec_from_file_location("eval_decision_cards", _P)
EV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EV)


@pytest.mark.parametrize("action,size,cond,fired,fwd,cap,verdict", [
    ("none", 0, 3, True, 3.5, 30, "missed"),
    ("none", 0, 1, False, 3.5, 10, "ok"),
    ("trim", 0, 2, False, 1.0, 10, "missed"),
    ("probe", 10, 3, True, -2.0, 30, "false_positive"),
    ("probe", 5, 3, True, 1.0, 10, "under_sized"),
    ("probe", 10, 3, True, 1.0, 10, "ok"),
    ("push", 30, 3, True, 2.0, 30, "ok"),
])
def test_classify(action, size, cond, fired, fwd, cap, verdict):
    v, note = EV.classify(action, size, cond, fired, fwd, cap)
    assert v == verdict, note


def test_pick_symbol():
    assert EV.pick_symbol("半导体 512480") == "512480.SH"
    assert EV.pick_symbol("PCB 落后") == "002384.SZ"
    assert EV.pick_symbol("无代码") == "000001.SH"
    assert EV.pick_symbol("600519 贵州茅台") == "600519.SH"

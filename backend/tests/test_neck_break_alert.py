# -*- coding: utf-8 -*-
"""颈线突破提醒的三段式判定（2026-11-04）。

为什么要这三段：只看盘中一瞬会被假突破骗（冲高回落最常见）。
他 2026-03-04：「缩量跌破不算…放量跌破就直接算」⇒ 跌回也要看量。
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "neck_alert", os.path.join(ROOT, "jobs", "wolf_neck_break_alert.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

NECK = 3708.20
ST = "双脚成立·三根K线出结构"


def _d(stage, above, below_fail, vr, fb, hm, px):
    return M.decide(stage, above, below_fail, vr, fb, M._hm2min(hm), hm, px, NECK, ST)


def test_first_volume_break_pushes_trial():
    st, msg = _d("NONE", True, False, 1.10, "", "1015", 3709.5)
    assert st == "BREAK" and msg and "试探" in msg[0]


def test_break_without_holding_does_not_push_again():
    st, msg = _d("BREAK", True, False, 1.00, "1015", "1025", 3712.0)
    assert st == "BREAK" and msg is None


def test_hold_after_30_minutes():
    st, msg = _d("BREAK", True, False, 1.05, "1015", "1050", 3715.0)
    assert st == "HOLD" and msg and "站稳" in msg[0]


def test_fail_on_volume_drop_back():
    st, msg = _d("HOLD", False, True, 1.20, "1015", "1100", 3692.0)
    assert st == "FAIL" and msg and "假突破" in msg[0]


def test_shrinking_drop_back_does_not_count():
    st, msg = _d("HOLD", False, True, 0.90, "1015", "1100", 3692.0)
    assert st == "HOLD" and msg is None


def test_close_above_neck_confirms_when_break_was_late():
    st, msg = _d("BREAK", True, False, 1.10, "1440", "1456", 3720.0)
    assert st == "HOLD_CLOSE" and msg and "收盘站上" in msg[0]


def test_hold_fires_before_close_when_already_held():
    st, msg = _d("BREAK", True, False, 1.10, "1015", "1456", 3720.0)
    assert st == "HOLD" and msg and "站稳" in msg[0]

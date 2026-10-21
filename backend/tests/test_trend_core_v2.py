# -*- coding: utf-8 -*-
"""trend_core v2 状态机单测。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'apps', 'main_line'))
from trend_core import CoreInputs, decide


def _all(**kw):
    base = dict(operation='build', mainline_confirmed=True, leader_ok=True, spread5=0.5,
                core_exists=False, core_pct=0.0)
    base.update(kw)
    return CoreInputs(**base)


def test_build_no_core():
    d = decide(_all())
    assert d.state == 'BUILD' and d.action == 'build'
    assert (d.total_target_pct, d.core_target_pct, d.t_target_pct, d.cash_pct) == (75.0, 50.0, 25.0, 25.0)


def test_build_add_to_target():
    d = decide(_all(core_exists=True, core_pct=30))
    assert d.state == 'HOLD' and d.action == 'add'
    assert d.core_target_pct == 50.0


def test_build_hold_at_target():
    d = decide(_all(core_exists=True, core_pct=50))
    assert d.state == 'HOLD' and d.action == 'hold'


def test_build_trim_above_target():
    d = decide(_all(core_exists=True, core_pct=60))
    assert d.state == 'TRIM' and d.action == 'trim'
    assert d.core_target_pct == 50.0


def test_side_targets():
    d = decide(_all(operation='side'))
    assert (d.total_target_pct, d.core_target_pct, d.t_target_pct, d.cash_pct) == (50.0, 30.0, 20.0, 50.0)


def test_defense_trims_to_15():
    d = decide(_all(operation='defense', core_exists=True, core_pct=50))
    assert d.state == 'TRIM' and d.action == 'trim'
    assert (d.total_target_pct, d.core_target_pct, d.t_target_pct, d.cash_pct) == (30.0, 15.0, 15.0, 70.0)


def test_exit_clears_core():
    d = decide(_all(operation='exit', core_exists=True, core_pct=50))
    assert d.state == 'EXIT' and d.action == 'exit' and d.core_target_pct == 0.0


def test_platform_break_half():
    d = decide(_all(core_exists=True, core_pct=50, break_flags=('platform_break',)))
    assert d.state == 'TRIM' and d.action == 'trim' and d.core_target_pct == 25.0


def test_double_top_half():
    d = decide(_all(core_exists=True, core_pct=40, break_flags=('double_top',)))
    assert d.core_target_pct == 20.0


def test_time_stop_third():
    d = decide(_all(core_exists=True, core_pct=60, break_flags=('time_stop',)))
    assert d.state == 'TRIM' and round(d.core_target_pct, 1) == 40.0


def test_premise_fail_no_core_off():
    d = decide(_all(mainline_confirmed=False))
    assert d.state == 'OFF' and d.core_target_pct == 0.0
    assert d.total_target_pct == 75.0 and d.t_target_pct == 75.0


def test_premise_fail_with_core_trim_half():
    d = decide(_all(mainline_confirmed=False, core_exists=True, core_pct=40))
    assert d.state == 'TRIM' and d.core_target_pct == 20.0


def test_regime_white_blocks_build():
    d = decide(_all(spread5=-0.3))
    assert d.state == 'OFF' and d.core_target_pct == 0.0


def test_t_only_no_core_off():
    d = decide(_all(operation='t_only'))
    assert d.state == 'OFF' and d.core_target_pct == 0.0


def test_t_only_trims_above_cap():
    d = decide(_all(operation='t_only', core_exists=True, core_pct=40))
    assert d.state == 'TRIM' and d.core_target_pct == 30.0


def test_invalid_operation_exit():
    d = decide(_all(operation='unknown', core_exists=True, core_pct=50))
    assert d.state == 'EXIT' and d.core_target_pct == 0.0

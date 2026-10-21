# -*- coding: utf-8 -*-
"""core_adapter 单测：市场级 core 决策 + 数据读取 fail-open。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'apps', 'main_line'))
import core_adapter


def test_build_all_premises():
    d = core_adapter.decide_market(operation='build', spread5=0.5, mainline_confirmed=True, leader_ok=True)
    assert d['state'] == 'BUILD' and d['core_target_pct'] == 50.0
    assert d['total_target_pct'] == 75.0


def test_defense_trims_to_15():
    d = core_adapter.decide_market(operation='defense', spread5=0.2, mainline_confirmed=True,
                                   leader_ok=True, core_exists=True, core_pct=40)
    assert d['state'] == 'TRIM' and d['core_target_pct'] == 15.0


def test_white_regime_blocks_build():
    d = core_adapter.decide_market(operation='build', spread5=-0.3, mainline_confirmed=True, leader_ok=True)
    assert d['state'] == 'OFF' and d['core_target_pct'] == 0.0


def test_missing_operation_falls_back_exit():
    d = core_adapter.decide_market(operation='', spread5=0.0)
    assert d['state'] == 'EXIT' and d['core_target_pct'] == 0.0


def test_read_spread5_available():
    v = core_adapter.read_spread5()
    assert v is None or isinstance(v, float)


def test_write_core_state_off_by_default(monkeypatch):
    monkeypatch.delenv('WOLF_CORE_V2', raising=False)
    assert core_adapter.write_core_state(operation='build', spread5=0.5) is None

# -*- coding: utf-8 -*-
"""单测：建仓两道真闸门（(e) 交叉格 / 抗跌）——2026-09-15 用户指示真对接生产。"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "apps" / "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

F = importlib.import_module("wolf_entry_filters")


def test_switches_default_on_and_env(monkeypatch):
    monkeypatch.delenv("WOLF_CLOSE_POS_GATE", raising=False)
    monkeypatch.delenv("WOLF_DEFENSIVE_GATE", raising=False)
    assert F.close_pos_enabled() is True and F.defensive_enabled() is True
    monkeypatch.setenv("WOLF_CLOSE_POS_GATE", "0")
    monkeypatch.setenv("WOLF_DEFENSIVE_GATE", "0")
    assert F.close_pos_enabled() is False and F.defensive_enabled() is False


def test_close_pos_cross_cell_blocks_only_that_cell():
    pool = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    cut = F.tercile_high(pool)
    hit, why = F.close_pos_blocked(cut + 0.5, 0.6, pool)      # 高档 ∧ 半强收盘 → 拦
    assert hit and "交叉格" in why
    assert F.close_pos_blocked(cut + 0.5, 0.9, pool)[0] is False   # 强收盘不拦
    assert F.close_pos_blocked(1.0, 0.6, pool)[0] is False         # 低档不拦
    assert F.close_pos_blocked(None, 0.6, pool)[0] is False        # 数据缺失放行
    assert F.close_pos_blocked(cut + 0.5, 0.6, [1, 2, 3])[0] is False  # 样本<6 不判


def test_defensive_blocks_weak_low_position():
    pool = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    cut = F.tercile_low(pool)
    hit, why = F.defensive_blocked(cut - 0.1, -3.0, -1.0, pool)   # 低位 ∧ 弱于大盘 → 拦
    assert hit and "抗跌" in why
    assert F.defensive_blocked(cut - 0.1, +2.0, -1.0, pool)[0] is False   # 低位但更强 → 放行
    assert F.defensive_blocked(8.0, -3.0, -1.0, pool)[0] is False         # 非低位 → 放行
    assert F.defensive_blocked(cut - 0.1, None, -1.0, pool)[0] is False   # 缺数放行


def test_pos_of_bounds():
    assert F.pos_of(5.0, 6.0, 4.0) == 0.5
    assert F.pos_of(5.0, 5.0, 5.0) is None

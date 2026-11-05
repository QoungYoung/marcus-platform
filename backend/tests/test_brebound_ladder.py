# -*- coding: utf-8 -*-
"""B反 分批仓位阶梯（2026-11-04）。

语料依据：
· 2026-11-04 13:44「双脚位置 缩转放 啧啧 仓位推上去咯」+ 13:59 半导体 5%→30%
· 2025-12-03「3888 收盘没站上 50%｜3850 收盘跌破 35%｜3816 企稳加回 50%｜站稳 3922-3930 70%」
  ⇒ 他的仓位是**收盘对关键位的阶梯函数**，不是固定 1/3
· 2026-11-03 半导体 5% + 国算 8% ≈ 底仓 10%（结构还没成立就先建底仓）
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "breb1450", os.path.join(ROOT, "jobs", "wolf_brebound_1450.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

L = M.brebound_ladder


def test_no_feet_no_conditions_is_zero():
    assert L(2, 0, False, False, feet_formed=False)["position_pct"] == 0


def test_feet_only_gets_base_tenth():
    # 11-03：右脚成立、三条件未满 ⇒ 底仓 10%（他半导体 5% + 国算 8%）
    assert L(2, 0, False, False, feet_formed=True)["position_pct"] == 10


def test_three_conditions_gets_30():
    # 11-04：三条件成立 ⇒ 30%（他半导体 5%→30%）
    assert L(3, 0, False, False, feet_formed=True)["position_pct"] == 30


def test_three_days_above_wave1_top_gets_50():
    assert L(3, 3, False, False, feet_formed=True)["position_pct"] == 50


def test_neck_break_gets_70():
    assert L(3, 3, True, False, feet_formed=True)["position_pct"] == 70


def test_broken_low_resets_to_zero():
    assert L(3, 3, True, True, feet_formed=True)["position_pct"] == 0


def test_ladder_is_monotonic():
    """档位必须单调递增，不能出现"加条件反而减仓"。"""
    seq = [L(0, 0, False, False, True)["position_pct"],
           L(3, 0, False, False, True)["position_pct"],
           L(3, 3, False, False, True)["position_pct"],
           L(3, 3, True, False, True)["position_pct"]]
    assert seq == sorted(seq) and seq == [10, 30, 50, 70]

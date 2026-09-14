# -*- coding: utf-8 -*-
"""G7（大涨日多卖/大跌日多买）+ G8（出上影线停机）单测 —— 2026-09-14。

狼大：2025-01-23「大涨之日少买票，多卖票，大跌之日多买票 少卖票」；
      2025-07-17「任何时候 看见机器人板块出上影线 立马停止做T，保持 30% 机器人底仓就别动了」。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_day_rules as DR  # noqa: E402


def test_day_bias_thresholds(monkeypatch):
    monkeypatch.delenv("WOLF_UP_DAY_PCT", raising=False)
    assert DR.day_bias(1.5) == "up"
    assert DR.day_bias(-1.5) == "down"
    assert DR.day_bias(0.2) == "neutral"
    assert DR.day_bias(None) == "neutral"          # 取不到 → 不猜
    monkeypatch.setenv("WOLF_UP_DAY_PCT", "2.0")
    assert DR.day_bias(1.5) == "neutral"
    monkeypatch.delenv("WOLF_UP_DAY_PCT", raising=False)


def test_up_day_lowers_tp_threshold(monkeypatch):
    monkeypatch.delenv("WOLF_UP_DAY_TP_FACTOR", raising=False)
    assert DR.tp_threshold(3.0, "up") == pytest.approx(2.01, abs=0.01)   # 3% × 0.67 → 2%
    assert DR.tp_threshold(3.0, "down") == 3.0
    assert DR.tp_threshold(3.0, "neutral") == 3.0
    monkeypatch.setenv("WOLF_UP_DAY_TP_FACTOR", "0.5")
    assert DR.tp_threshold(4.0, "up") == pytest.approx(2.0)
    monkeypatch.delenv("WOLF_UP_DAY_TP_FACTOR", raising=False)


def test_down_day_blocks_new_realize_legs():
    assert DR.allow_realize_sell("down") is False      # 少卖票（保护性卖出不走这个门）
    assert DR.allow_realize_sell("up") is True
    assert DR.allow_realize_sell("neutral") is True


def test_upper_shadow_detection(monkeypatch):
    monkeypatch.delenv("WOLF_SHADOW_RATIO", raising=False)
    up = {"open": 10.0, "high": 11.0, "low": 9.9, "close": 10.1}     # 长上影
    flat = {"open": 10.0, "high": 10.35, "low": 9.9, "close": 10.3}  # 短上影（上影 0.05 / 全幅 0.45 ≈ 11%）
    assert DR.has_upper_shadow(up) is True
    assert DR.has_upper_shadow(flat) is False
    assert DR.has_upper_shadow(None) is False
    assert DR.has_upper_shadow({"open": 10, "high": 10, "low": 10, "close": 10}) is False   # h<=l 不算
    mid = {"open": 10.0, "high": 10.32, "low": 9.9, "close": 10.1}    # 上影 0.22 / 全幅 0.42 ≈ 52%
    assert DR.has_upper_shadow(mid) is True                          # 默认 30% 阈值下算上影
    monkeypatch.setenv("WOLF_SHADOW_RATIO", "0.6")
    assert DR.has_upper_shadow(mid) is False                         # 阈值提到 60% → 不算
    assert DR.has_upper_shadow(up) is True                           # 82% 的上影仍然算
    monkeypatch.delenv("WOLF_SHADOW_RATIO", raising=False)


def test_shadow_stop_reads_last_completed_bar():
    bars = [{"open": 10, "high": 10.6, "low": 9.0, "close": 10.5},   # 短上影（≈6%）
            {"open": 10, "high": 11.0, "low": 9.9, "close": 10.1}]     # 最后一根长上影
    stop, why = DR.shadow_stop(bars, 1)
    assert stop is True and "上影线" in why
    stop2, _ = DR.shadow_stop([bars[0]], 1)
    assert stop2 is False
    assert DR.shadow_stop([], 1)[0] is False        # 取不到日线 → 不停机


def test_switch_off(monkeypatch):
    monkeypatch.setenv("WOLF_DAY_RULES", "0")
    assert DR.enabled() is False
    monkeypatch.delenv("WOLF_DAY_RULES", raising=False)
    assert DR.enabled() is True


def test_monitor_wiring():
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor)
    assert "_index_pct_today" in src and "allow_realize_sell" in src and "shadow_stop" in src
    assert "tp_threshold" in src

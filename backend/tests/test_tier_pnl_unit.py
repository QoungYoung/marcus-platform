# -*- coding: utf-8 -*-
"""加仓层级评估的浮盈单位口径单测（2026-09-17，P0 缺陷 5）。

## 缺陷（修复前）

`position_tier_monitor.evaluate_position_tier` 用**量级猜单位**：
    `pnl_pct = float_pnl_pct / 100.0 if abs(float_pnl_pct) > 1 else float_pnl_pct`
而两个调用点传进来的都是**百分数**（`(price-avg)/avg*100`）。于是 |浮盈| ≤ 1% 时被当成"已是小数"：
  · +0.5% → 0.5（=50%）→ `pnl_pct >= 0.03` 成立 → **UPGRADE_TO_SPRINT（25% 上限档）**；
  · 也就是说"刚有一点浮盈"被读成"浮盈 50%"，直接跳级到最高档加仓评估。
该监控门控通过后**绕过 T 网关直接 `executor.buy()`**（生产 20260727–20260901 期间是活的）⇒ 影响真实下单。

## 修法

口径唯一：入参 = 百分数 → 一律 `/100`（`float(float_pnl_pct or 0.0) / 100.0`）。docstring 写明契约。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import pytest  # noqa: E402
import app.services.position_tier_monitor as M  # noqa: E402


@pytest.fixture
def mon():
    # 只测无状态的纯评估方法，绕开 __init__（它要读档位状态文件 / 建日志目录）
    return M.PositionTierMonitor.__new__(M.PositionTierMonitor)


@pytest.mark.parametrize("pnl_pct,expect", [
    # 边界四个（用户点名的 0.5 / 1.0 / 1.5 / -0.5）
    (0.5, "HOLD"),                    # 修复前 = UPGRADE_TO_SPRINT（0.5 被当成 50%）
    (1.0, "UPGRADE_TO_CONFIRM"),      # ≥1% → 确认仓
    (1.5, "UPGRADE_TO_CONFIRM"),
    (-0.5, "HOLD"),                   # 修复前 = HOLD（但口径同样错：-0.5 被当成 -50%）
    # 阈值两侧（口径修复后 u=百分数）
    (0.99, "HOLD"), (2.99, "UPGRADE_TO_CONFIRM"), (3.0, "UPGRADE_TO_SPRINT"),
    (5.0, "UPGRADE_TO_SPRINT"), (-3.0, "HOLD"),
    # 大于 1 的输入修复前后一致（回归锚：别把旧行为改坏）
    (2.5, "UPGRADE_TO_CONFIRM"), (10.0, "UPGRADE_TO_SPRINT"),
])
def test_probe_tier_pnl_unit(mon, pnl_pct, expect):
    """试探仓：0.5%/1.0%/1.5%/-0.5% 及阈值两侧都按"百分数"解释。"""
    ev = mon.evaluate_position_tier("SZ300750", pnl_pct, "probe")
    assert ev.action == expect, (pnl_pct, ev.action, ev.signal)
    # signal 里的百分比必须是"人话"（0.5% → 显示 0.5%，而不是 50.0%）
    if pnl_pct == 0.5:
        assert "0.5%" in ev.signal, ev.signal


def test_confirm_tier_pnl_unit(mon):
    """确认仓 → 冲刺仓阈值 3%：2.9% 不升级、3.0% 升级。"""
    assert mon.evaluate_position_tier("SZ300750", 2.9, "confirm").action == "HOLD"
    assert mon.evaluate_position_tier("SZ300750", 3.0, "confirm").action == "UPGRADE_TO_SPRINT"
    assert mon.evaluate_position_tier("SZ300750", 0.5, "confirm").action == "HOLD"


def test_sprint_tier_is_max(mon):
    assert mon.evaluate_position_tier("SZ300750", 0.5, "sprint").action == "MAX_TIER"


def test_zero_and_none_are_hold(mon):
    assert mon.evaluate_position_tier("SZ300750", 0, "probe").action == "HOLD"
    assert mon.evaluate_position_tier("SZ300750", None, "probe").action == "HOLD"


def test_signal_text_uses_percent_not_fraction(mon):
    """信号文案是给 AI/人看的：1.5% 要显示 1.5%，不能显示 150%。"""
    ev = mon.evaluate_position_tier("SZ300750", 1.5, "probe")
    assert "1.5%" in ev.signal, ev.signal
    ev = mon.evaluate_position_tier("SZ300750", 3.24, "probe")
    assert "3.2%" in ev.signal, ev.signal

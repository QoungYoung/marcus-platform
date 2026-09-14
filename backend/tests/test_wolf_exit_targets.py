# -*- coding: utf-8 -*-
"""G2 / G4 单测 —— 2026-09-14。

G2 个股止盈点 = 前一波拉升幅度的 0.618 位（狼大 2026-04-23）；
G4 被动止盈线「只上移、不破不卖」（2025-06-09 / 2025-07-17 / 2026-07-01）。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_fib_target as FT  # noqa: E402
from app.services import wolf_passive_stop as PS  # noqa: E402


def _bar(d, o, h, l, c, v=1000.0):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "vol": v}


def _zigzag():
    """构造一段：10 → 跌到 8（低点）→ 涨到 12（高点）→ 回落到 9.5（当前）。"""
    bars = []
    px = 10.0
    for i in range(6):                      # 下行到 8
        px -= 0.4
        bars.append(_bar("d%02d" % i, px, px + 0.1, px - 0.1, px))
    bars.append(_bar("low", 7.8, 8.0, 7.6, 8.0))     # 摆动低点 7.6
    for i in range(6):                      # 上行到 12
        px += 0.65
        bars.append(_bar("u%02d" % i, px, px + 0.1, px - 0.1, px))
    bars.append(_bar("high", 11.9, 12.0, 11.8, 12.0))  # 摆动高点 12.0
    for i in range(5):                      # 回落到 9.5
        px -= 0.5
        bars.append(_bar("p%02d" % i, px, px + 0.1, px - 0.1, px))
    return bars


def test_g2_prev_up_leg_and_target():
    bars = _zigzag()
    i = len(bars) - 1
    leg = FT.prev_up_leg(bars, i, k=2, lookback=60)
    assert leg is not None
    lidx, lpx, hidx, hpx = leg
    # 检测出的应是"低点在前、高点在后"的完整上涨波
    assert lidx < hidx and hpx > lpx
    tgt = FT.fib_target(bars, i, k=2, lookback=60)
    # 自洽：0.618 位 = 低点 + 0.618×(高 − 低)
    assert tgt["target"] == pytest.approx(round(lpx + 0.618 * (hpx - lpx), 4))
    assert tgt["leg_low"] == pytest.approx(lpx) and tgt["leg_high"] == pytest.approx(hpx)


def test_g2_decision_requires_profit_and_target(monkeypatch):
    monkeypatch.delenv("WOLF_FIB_TARGET", raising=False)
    assert FT.enabled() is True
    target = FT.fib_target(_zigzag(), len(_zigzag()) - 1, k=2, lookback=60)["target"]
    act, _ = FT.fib_decision(target + 0.01, target, cost=target - 1.0)      # 到点且有浮盈 → 卖
    assert act == "sell"
    act, why = FT.fib_decision(target + 0.01, target, cost=target + 0.5)    # 到点但无浮盈 → 不卖
    assert act == "wait" and "无浮盈" in why
    act, why = FT.fib_decision(target - 0.5, target, cost=target - 1.0)     # 未到点
    assert act == "wait" and "止盈位" in why
    act, why = FT.fib_decision(10.0, None, cost=9.0)       # 没有波段 → 不动作
    assert act == "wait" and "波段" in why
    monkeypatch.setenv("WOLF_FIB_TARGET", "0")
    assert FT.enabled() is False


def test_g2_ratio_configurable(monkeypatch):
    bars = _zigzag()
    i = len(bars) - 1
    base = FT.fib_target(bars, i, k=2, lookback=60)
    monkeypatch.setenv("WOLF_FIB_RATIO", "0.382")
    tgt = FT.fib_target(bars, i, k=2, lookback=60)
    assert tgt["target"] == pytest.approx(round(base["leg_low"] + 0.382 * (base["leg_high"] - base["leg_low"]), 4))
    assert tgt["target"] < base["target"]          # 0.382 位低于 0.618 位
    monkeypatch.delenv("WOLF_FIB_RATIO", raising=False)


def _tmp_ps(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return importlib.reload(PS)


def test_g4_line_only_moves_up(tmp_path, monkeypatch):
    ps = _tmp_ps(tmp_path, monkeypatch)
    assert ps.update_line(None, 9.5) == 9.5
    assert ps.update_line(9.5, 9.2) == 9.5          # **只上移**：候选更低也不下移
    assert ps.update_line(9.5, 10.1) == 10.1
    bars = [_bar("d%d" % i, 10, 10.5, 9.0 + i * 0.05, 10) for i in range(20)]
    line = ps.sync_line("stock", "SH600000", bars, profit_pct=10.0)
    assert line == pytest.approx(min(b["low"] for b in bars[-13:]))
    # 更高的低点 → 线上移
    bars2 = bars + [_bar("e", 10.2, 10.6, 9.9, 10.4)]
    line2 = ps.sync_line("stock", "SH600000", bars2, profit_pct=10.0)
    assert line2 >= line
    # 记录在盘面上（同一 key）
    assert ps.get_line("stock", "SH600000") == line2


def test_g4_high_profit_uses_ma13_and_mid(tmp_path, monkeypatch):
    ps = _tmp_ps(tmp_path, monkeypatch)
    bars = [_bar("d%d" % i, 10, 10.2, 9.0, 11.0) for i in range(25)]   # 收盘 11、最低 9
    c_low = ps.candidate_line(bars, profit_pct=10.0)                    # 普通：近 13 日最低 = 9
    c_hi = ps.candidate_line(bars, profit_pct=150.0)                    # 浮盈>100%：并用 MA13/中轨 = 11
    assert c_low == pytest.approx(9.0) and c_hi == pytest.approx(11.0)


def test_g4_decision_and_time_gate(tmp_path, monkeypatch):
    ps = _tmp_ps(tmp_path, monkeypatch)
    act, why = ps.passive_decision(9.0, 9.5, cost=8.0)          # 跌破线 + 有浮盈 → 卖
    assert act == "sell" and "不破不卖" in why
    assert ps.passive_decision(9.0, 9.5, cost=9.2)[0] == "wait"  # 无浮盈
    assert ps.passive_decision(9.6, 9.5, cost=8.0)[0] == "wait"  # 未破线
    assert ps.passive_decision(9.0, 9.5, cost=8.0, time_ok=False)[0] == "wait"   # 时点门
    monkeypatch.setenv("WOLF_PASSIVE_STOP", "0")
    assert ps.enabled() is False

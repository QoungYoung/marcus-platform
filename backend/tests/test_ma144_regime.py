# -*- coding: utf-8 -*-
"""`wolf_ma144_regime`（144 线大级别资格，影子优先）定向单测 —— 2026-09-15 round 10。

覆盖：MA144/斜率/上方判定（纯函数，合成序列）、数据不足 fail-open、开关默认值（gate 关 / shadow 开）、
影子文件内容（含"当日其它门状态"，供增量对照）、臂内接线（源码级）。
"""
import importlib.util
import inspect
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "jobs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wolf_ma144_regime as M  # noqa: E402


def _rows(n, start=100.0, step=0.0):
    """n 天序列：第 i 天收盘 = start + i*step（step>0 上行）。"""
    return [("%08d" % (20250101 + i), start + i * step) for i in range(n)]


def test_state_uptrend_allows():
    st = M.state_from(_rows(200, 100.0, 0.5), win=20)
    assert st["ok"] and st["n"] == 200
    assert st["slope_pct"] > 0 and st["allow_slope"] is True
    assert st["above"] is True
    assert M.allow(st) is True


def test_state_downtrend_blocks():
    st = M.state_from(_rows(200, 200.0, -0.5), win=20)
    assert st["slope_pct"] < 0 and st["allow_slope"] is False
    assert M.allow(st) is False


def test_flat_series_is_allowed():
    """走平（斜率=0）→ 按他的话「稍微走平就可以做波段」应放行。"""
    st = M.state_from(_rows(200, 100.0, 0.0), win=20)
    assert st["slope_pct"] == pytest.approx(0.0, abs=1e-9)
    assert M.allow(st) is True


def test_insufficient_data_fails_open():
    """序列不足 144 天 → ok=False 且 allow() 必须放行（绝不因读数失败停买）。"""
    st = M.state_from(_rows(100), win=20)
    assert st["ok"] is False
    assert M.allow(st) is True
    assert M.allow({"ok": False}) is True


def test_slope_none_falls_back_to_above():
    """序列够 144 但不够 144+win（斜率算不出）→ 退化成"收盘 ≥ MA144"口径。"""
    rows = _rows(150, 200.0, -0.5)
    st = M.state_from(rows, win=20)
    assert st["ok"] and st["slope_pct"] is None
    assert st["above"] is False and M.allow(st) is False


def test_switches_default(monkeypatch):
    monkeypatch.delenv("WOLF_MA144_GATE", raising=False)
    monkeypatch.delenv("WOLF_MA144_SHADOW", raising=False)
    monkeypatch.delenv("WOLF_MA144_SLOPE_WIN", raising=False)
    monkeypatch.delenv("WOLF_MA144_INDEX", raising=False)
    assert M.gate_enabled() is False           # 闸门默认关（证据不足以开闸）
    assert M.shadow_enabled() is True          # 影子默认开（零行为变化）
    assert M.slope_win() == 20 and M.index_code() == "000001.SH"
    monkeypatch.setenv("WOLF_MA144_GATE", "1")
    monkeypatch.setenv("WOLF_MA144_SLOPE_WIN", "5")
    assert M.gate_enabled() is True and M.slope_win() == 5


def test_shadow_record_content(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_MA144_SHADOW", "1")
    monkeypatch.setattr(M, "closes", lambda code=None, refresh=True: _rows(200, 100.0, 0.5))
    fn = M.shadow_record({"wave_op": "side", "gate_blocked": ["半导体/芯片"], "buy_legs": ["SH600584"]})
    assert fn and os.path.exists(fn)
    d = json.load(open(fn, encoding="utf-8"))
    assert d["mode"] == "shadow" and d["state"]["ok"] is True
    # 影子必须带"当日其它门状态"→ 下一轮才能做增量对照
    assert d["extra"]["wave_op"] == "side"
    assert d["extra"]["gate_blocked"] == ["半导体/芯片"]
    assert d["extra"]["buy_legs"] == ["SH600584"]


def test_shadow_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_MA144_SHADOW", "0")
    assert M.shadow_record({}) is None
    assert not list(tmp_path.glob("ma144_shadow_*.json"))


def test_arm_is_wired_shadow_first():
    """臂内接线：引用模块 + 影子记录 + 闸门默认关（只在 gate_enabled() 时才清空买腿）。"""
    src = open(os.path.join(ROOT, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
    assert "wolf_ma144_regime" in src
    assert "shadow_record" in src and "gate_enabled" in src
    assert "MA144_GATE 拦下" in src
    # 影子先记（raw_legs_n），再判断闸门 → 保证影子不受闸门影响
    assert src.index("shadow_record(") < src.index("MA144_GATE 拦下")


def test_gate_mode_default_is_above_and_switchable(monkeypatch):
    """2026-09-15 拍板：默认口径改挂 ② 收盘 ≥ MA144；置 slope 回到旧口径。"""
    import importlib
    M = importlib.import_module("wolf_ma144_regime")
    monkeypatch.delenv("WOLF_MA144_MODE", raising=False)
    assert M.gate_mode() == "above"
    st = {"ok": True, "above": False, "allow_slope": True}      # 收盘在下方但斜率达标
    assert M.allow(st) is False                                 # 新口径：收盘下 → 拦
    monkeypatch.setenv("WOLF_MA144_MODE", "slope")
    assert M.allow(st) is True                                  # 旧口径：斜率达标 → 放行
    monkeypatch.delenv("WOLF_MA144_MODE", raising=False)
    assert M.allow({"ok": False}) is True                       # 数据缺失仍 fail-open

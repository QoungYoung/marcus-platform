# -*- coding: utf-8 -*-
"""`wolf_line_regime`（白线在上 ∧ 缩量 → 没有买点）定向单测 —— 2026-09-15 round 17。

覆盖：缩量判据（纯函数）、黄白线组合的"没有买点"判定与 fail-open、开关默认值、
影子文件内容（含当日其它门状态）、臂内接线（源码级：影子先于闸门）。
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "jobs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wolf_line_regime as M  # noqa: E402


def _rows(amounts):
    return [("%08d" % (20260101 + i), 3000.0 + i, float(a)) for i, a in enumerate(amounts)]


def test_shrink_from():
    assert M.shrink_from(_rows([100, 80]))["shrink"] is True
    assert M.shrink_from(_rows([100, 120]))["shrink"] is False
    assert M.shrink_from(_rows([100])) is None          # 不足两日
    assert M.shrink_from(_rows([0, 80])) is None        # 前值为 0 → 不可比


def test_no_buy_only_when_bai_and_shrink(monkeypatch):
    monkeypatch.setattr(M, "series", lambda code=None, refresh=True: _rows([100, 80]))
    monkeypatch.setattr(M, "huang_bai_side", lambda: {"side": "bai", "spread": 0.3})
    st = M.state()
    assert st["no_buy"] is True and M.allow(st) is False

    monkeypatch.setattr(M, "huang_bai_side", lambda: {"side": "huang", "spread": -0.3})
    assert M.state()["no_buy"] is False and M.allow(M.state()) is True

    monkeypatch.setattr(M, "series", lambda code=None, refresh=True: _rows([100, 120]))
    monkeypatch.setattr(M, "huang_bai_side", lambda: {"side": "bai", "spread": 0.3})
    assert M.state()["no_buy"] is False                  # 白线在上但放量 → 不拦


def test_fail_open_when_data_missing(monkeypatch):
    """黄白线取不到 或 成交额不可比 → 一律放行（绝不因读数失败停买）。"""
    monkeypatch.setattr(M, "series", lambda code=None, refresh=True: _rows([100, 80]))
    monkeypatch.setattr(M, "huang_bai_side", lambda: {"side": None, "spread": None})
    st = M.state()
    assert st["no_buy"] is False and M.allow(st) is True
    assert M.allow({"side": None, "shrink": True}) is True
    assert M.allow({"side": "bai", "shrink": None}) is True


def test_switches_default(monkeypatch):
    monkeypatch.delenv("WOLF_LINE_REGIME_GATE", raising=False)
    monkeypatch.delenv("WOLF_LINE_REGIME_SHADOW", raising=False)
    monkeypatch.delenv("WOLF_LINE_REGIME_INDEX", raising=False)
    assert M.gate_enabled() is False       # 闸门默认关（证据不足，先影子）
    assert M.shadow_enabled() is True      # 影子默认开（零行为变化）
    assert M.index_code() == "000001.SH"
    monkeypatch.setenv("WOLF_LINE_REGIME_GATE", "1")
    assert M.gate_enabled() is True


def test_shadow_record_content(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setattr(M, "series", lambda code=None, refresh=True: _rows([100, 80]))
    monkeypatch.setattr(M, "huang_bai_side", lambda: {"side": "bai", "spread": 0.3})
    monkeypatch.setenv("WOLF_LINE_REGIME_SHADOW", "1")
    fn = M.shadow_record({"wave_op": "side", "gate_blocked": ["半导体/芯片"], "buy_legs": ["SH600584"]})
    assert fn and os.path.exists(fn)
    d = json.load(open(fn, encoding="utf-8"))
    assert d["mode"] == "shadow" and d["state"]["no_buy"] is True
    assert d["extra"]["wave_op"] == "side" and d["extra"]["buy_legs"] == ["SH600584"]


def test_shadow_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_LINE_REGIME_SHADOW", "0")
    assert M.shadow_record({}) is None
    assert not list(tmp_path.glob("line_regime_shadow_*.json"))


def test_arm_wired_shadow_first():
    src = open(os.path.join(ROOT, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
    assert "wolf_line_regime" in src and "shadow_record" in src and "gate_enabled" in src
    assert "LINE_REGIME_GATE 拦下" in src
    assert src.index("shadow_record({") < src.index("LINE_REGIME_GATE 拦下")

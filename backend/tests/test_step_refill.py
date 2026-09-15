# -*- coding: utf-8 -*-
"""`wolf_step_refill`（C6 分步回补条件：时间窗 ∧ 缩量）定向单测 —— 2026-09-15 round 20。

覆盖：窗口/量能的四种组合、**数据缺失 fail-open**、开关默认值（闸门关 / 影子开）、
影子文件的序列累积、t_monitor 接线（源码级：影子先于闸门、且只在"要下单前"判条件）。
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _load():
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "wolf_step_refill_ut", os.path.join(ROOT, "backend", "app", "services", "wolf_step_refill.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def test_shrink_is_the_default_condition(monkeypatch):
    """默认**只用缩量**（时间窗已被证据否掉并被用户关闭，不叠加）。"""
    mod = _load()
    monkeypatch.delenv("WOLF_STEP_REFILL_WINDOW", raising=False)
    monkeypatch.setattr(mod, "_window", lambda: {"in_window": False, "why": "11:19 不在窗口", "windows": []})
    monkeypatch.setattr(mod, "_vol", lambda: {"vol_ratio": 0.7, "vol_kind": "shrink"})
    st = mod.state()
    assert st["window_required"] is False
    assert st["allow"] is True and "不叠加" in st["why"]

    monkeypatch.setattr(mod, "_vol", lambda: {"vol_ratio": 1.4, "vol_kind": "expand"})
    st = mod.state()
    assert st["allow"] is False and "缩量" in st["why"]


def test_window_can_be_stacked_back(monkeypatch):
    """打开子开关后，窗口才参与判定（他的话原样）。"""
    mod = _load()
    monkeypatch.setenv("WOLF_STEP_REFILL_WINDOW", "1")
    monkeypatch.setattr(mod, "_window", lambda: {"in_window": False, "why": "11:19", "windows": []})
    monkeypatch.setattr(mod, "_vol", lambda: {"vol_ratio": 0.7, "vol_kind": "shrink"})
    st = mod.state()
    assert st["window_required"] is True and st["allow"] is False and "时间窗" in st["why"]
    monkeypatch.setattr(mod, "_window", lambda: {"in_window": True, "why": "1415", "windows": []})
    assert mod.state()["allow"] is True


def test_window_membership_independent_of_a5_switch(monkeypatch):
    """窗口判定必须独立于 A5 的整体开关（生产实测 WOLF_TRADE_WINDOW=0，A5 关着）。"""
    mod = _load()
    monkeypatch.setenv("WOLF_TRADE_WINDOW", "0")
    w_in = mod._window("1415")
    w_out = mod._window("1100")
    assert w_in["in_window"] is True and w_out["in_window"] is False


def test_fail_open_when_data_missing(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_window", lambda: {"in_window": None, "why": "no", "windows": None})
    monkeypatch.setattr(mod, "_vol", lambda: {"vol_ratio": None, "vol_kind": None})
    st = mod.state()
    assert st["allow"] is True and "fail-open" in st["why"]
    assert mod.allow({"allow": True}) is True


def test_switches_default(monkeypatch):
    mod = _load()
    monkeypatch.delenv("WOLF_STEP_REFILL", raising=False)
    monkeypatch.delenv("WOLF_STEP_REFILL_SHADOW", raising=False)
    assert mod.enabled() is False       # 闸门默认关（只记录）
    assert mod.shadow_enabled() is True
    monkeypatch.setenv("WOLF_STEP_REFILL", "1")
    assert mod.enabled() is True


def test_shadow_records_sequence(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "DATA", str(tmp_path))
    monkeypatch.setattr(mod, "_window", lambda: {"in_window": True, "why": "ok", "windows": []})
    monkeypatch.setattr(mod, "_vol", lambda: {"vol_ratio": 0.6, "vol_kind": "shrink"})
    f1 = mod.shadow_record({"candidates": ["SH600584"]})
    f2 = mod.shadow_record({"candidates": ["SH600584"]})
    assert f1 == f2 and os.path.exists(f1)
    d = json.load(open(f1, encoding="utf-8"))
    assert d["mode"] == "shadow" and len(d["seq"]) == 2
    assert d["seq"][-1]["in_window"] is True and d["seq"][-1]["allow"] is True
    assert d["seq"][-1]["candidates"] == ["SH600584"]


def test_shadow_off_writes_nothing(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_STEP_REFILL_SHADOW", "0")
    assert mod.shadow_record({}) is None
    assert not list(tmp_path.glob("step_refill_shadow_*.json"))


def test_t_monitor_is_wired_shadow_first():
    src = open(os.path.join(ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "wolf_step_refill" in src
    assert src.index("SR.shadow_record(") < src.index("SR.enabled() and not SR.allow(")
    assert "C6 回补条件不满足" in src

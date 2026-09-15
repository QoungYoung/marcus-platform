# -*- coding: utf-8 -*-
"""`wolf_market_volume`（大盘级缩量/地量，纯影子）定向单测 —— 2026-09-15 round 22。

覆盖：档位划分（阈值全部来自他的话）、两市成交额合成与单位换算（千元→亿元→WE）、
日间缩量判定、数据缺失不猜（ok=False）、开关默认值、影子文件、臂内接线（源码级）。
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "wolf_market_volume_ut", os.path.join(ROOT, "apps", "main_line", "wolf_market_volume.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def test_bucket_thresholds_are_his():
    """档位阈值全部来自他的话：1WE / 1.5WE / 2WE。"""
    assert M.bucket_of(0.9).startswith("极地量")
    assert M.bucket_of(1.2).startswith("地量")
    assert M.bucket_of(1.8).startswith("缩量")
    assert M.bucket_of(2.5).startswith("常态")
    assert M.bucket_of(None) is None


def test_state_units_and_shrink():
    """千元 → 亿元 → 万亿；合成两市；日间缩量。"""
    ser = {"000001.SH": [("20260911", 9.5e8), ("20260914", 7.79e8)],
           "399106.SZ": [("20260911", 1.0137e9), ("20260914", 8.499e8)]}
    st = M.state(ser)
    assert st["ok"] is True and st["date"] == "20260914"
    # 7.79e8 千元 = 7790 亿元；+ 8499 亿 ≈ 16289 亿 ≈ 1.629WE
    assert 1.60 < st["we"] < 1.66
    assert st["shrink_vs_prev"] is True and st["ratio_vs_prev"] < 1
    assert st["bucket"].startswith("缩量")


def test_state_missing_data_does_not_guess(monkeypatch):
    assert M.state({})["ok"] is False
    assert M.state({"000001.SH": [("20260914", 1.0)]})["ok"] is False       # 只有一个指数
    assert M.state({"000001.SH": [("20260914", 1.0)], "399106.SZ": [("20260914", 1.0)]})["ok"] is False  # 共同交易日<2


def test_switches_default(monkeypatch):
    monkeypatch.delenv("WOLF_MARKET_VOL_SHADOW", raising=False)
    monkeypatch.delenv("WOLF_MARKET_VOL_GATE", raising=False)
    assert M.shadow_enabled() is True and M.enabled() is False


def test_shadow_record(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setattr(M, "state", lambda ser=None: {"ok": True, "date": "20260914", "we": 1.63,
                                                      "bucket": "缩量(1.5-2WE)", "shrink_vs_prev": True})
    fn = M.shadow_record({"wave_op": "side", "buy_legs": ["SH600584"]})
    assert fn and os.path.exists(fn)
    d = json.load(open(fn, encoding="utf-8"))
    assert d["mode"] == "shadow" and d["state"]["we"] == 1.63
    assert d["extra"]["wave_op"] == "side"


def test_shadow_off(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_MARKET_VOL_SHADOW", "0")
    assert M.shadow_record({}) is None


def test_arm_wired():
    src = open(os.path.join(ROOT, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
    assert "wolf_market_volume" in src and "MARKET_VOL we=" in src
    assert "shadow_record" in src

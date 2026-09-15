# -*- coding: utf-8 -*-
"""`wolf_ma_line_entry`（均线挂单影子）定向单测 —— 2026-09-15 round 12。

覆盖：线上/线下取"最近一条均线"的判定、样本不足、开关默认值（影子开 / 真挂腿关）、
影子文件内容与多次写同一日按链合并、臂内接线（源码级）。
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

import wolf_ma_line_entry as M  # noqa: E402


def test_nearest_line_below_picks_highest_below_price():
    # 前 130 天 =100，最后 30 天从 100 涨到 130 → MA13 最高(~124)、MA34 次之、MA60/MA144 ~100
    closes = [100.0] * 130 + [100.0 + i for i in range(1, 31)]
    px = closes[-1]
    line = M.nearest_line_below(closes, px)
    assert line is not None and line["k"] == 13
    assert line["v"] < px and line["dist_pct"] > 0


def test_no_line_when_price_below_all_ma():
    """一路下跌：所有均线都在价格上方 → 没有"下方最近的线"（不下单）。"""
    closes = [200.0 - i for i in range(160)]
    assert M.nearest_line_below(closes) is None


def test_insufficient_bars():
    assert M.nearest_line_below([100.0] * 5) is None      # < min(LINES)=13
    assert M.nearest_line_below([]) is None


def test_144_can_be_the_nearest_below():
    """长牛后回调：价格在其 13/34/60 日线**下方**、但在 144 日线**上方** → 只应取到 144 日线。"""
    closes = [50.0 + i * (150.0 / 139.0) for i in range(140)] + [190.0, 180.0, 165.0, 150.0]
    line = M.nearest_line_below(closes)
    assert line is not None and line["k"] == 144 and line["v"] <= closes[-1]


def test_switches_default(monkeypatch):
    monkeypatch.delenv("WOLF_MA_LINE_SHADOW", raising=False)
    monkeypatch.delenv("WOLF_MA_LINE_ENTRY", raising=False)
    assert M.shadow_enabled() is True     # 影子默认开（零行为变化）
    assert M.entry_enabled() is False     # 真挂腿默认关（需拍板）
    monkeypatch.setenv("WOLF_MA_LINE_ENTRY", "1")
    assert M.entry_enabled() is True


def test_shadow_record_merges_chains(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_MA_LINE_SHADOW", "1")
    f1 = M.shadow_record("Chiplet概念", [{"symbol": "SZ002156", "ma_line_k": 34, "ma_line_v": 10.5}])
    f2 = M.shadow_record("光刻机(胶)", [{"symbol": "SH603823", "ma_line_k": 60, "ma_line_v": 20.1}])
    assert f1 == f2 and os.path.exists(f1)
    d = json.load(open(f1, encoding="utf-8"))
    assert d["mode"] == "shadow" and list(d["lines"]) == [13, 34, 60, 144]
    assert set(d["chains"].keys()) == {"Chiplet概念", "光刻机(胶)"}
    assert d["chains"]["Chiplet概念"]["legs"][0]["symbol"] == "SZ002156"


def test_shadow_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_MA_LINE_SHADOW", "0")
    assert M.shadow_record("x", []) is None
    assert not list(tmp_path.glob("ma_line_shadow_*.json"))


def test_leg_info_shape():
    closes = [100.0] * 140 + [110.0]        # 价格在均线上方 → 有"下方最近的线"
    rec = M.leg_info("SH600000", closes, {"extra": 1})
    assert rec["symbol"] == "SH600000" and rec["bars"] == 141 and rec["extra"] == 1
    assert rec["would_place"] is True and rec["line"]["k"] in M.LINES
    # 价格在**所有**均线下方 → 不下单（would_place=False）
    down = M.leg_info("SH600000", [100.0] * 140 + [90.0])
    assert down["would_place"] is False and down["line"] is None


def test_arm_is_wired_shadow_only():
    """臂内接线：引用模块 + 影子记录；且**真挂腿开关默认关**（不改决策）。"""
    src = open(os.path.join(ROOT, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
    assert "wolf_ma_line_entry" in src and "shadow_record" in src
    assert "MA_LINE_SHADOW_ERR" in src
    # 影子记录发生在 _select_by_gate 之后；且臂内不引用 entry_enabled（=不真挂腿）
    assert "entry_enabled" not in src

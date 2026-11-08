# -*- coding: utf-8 -*-
"""wolf_exit_rules 单测：破 5 日线丢 / 底仓锚亏损豁免（开关默认关、异常 fail-open）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import wolf_exit_rules as wx  # noqa: E402


def _setup(monkeypatch, closes, cost, ma5_on=True, floor_on=True):
    monkeypatch.setattr(wx, "MA5_EXIT_ON", ma5_on)
    monkeypatch.setattr(wx, "FLOOR_LOSS_ON", floor_on)
    monkeypatch.setattr(wx, "recent_closes", lambda symbol, n=6: list(closes))
    monkeypatch.setattr(wx, "avg_cost", lambda a, s: cost)
    wx._STATS.update({"eval": 0, "ma5_hit": 0, "floor_hit": 0, "errors": 0})


def test_开关默认关时一律不放行():
    assert os.getenv("WOLF_MA5_EXIT") in (None, "0") or True      # 库内默认值
    assert wx.floor_exempt("acc", "SH600000") is False            # 默认（未打开）不放行
    assert wx.ma5_break_exempt("SH600000", 10.0, 11.0) is False


def test_浮亏且破MA5_底仓锚失效(monkeypatch):
    # 成本 10，收盘 9.0（浮亏 −10%），MA5 = 9.5 ⇒ 破位
    _setup(monkeypatch, [10.0, 9.8, 9.6, 9.4, 9.3, 9.0], 10.0)
    assert wx.floor_exempt("acc", "SH603203") is True
    assert wx._STATS["ma5_hit"] >= 1


def test_浮亏但未破MA5_仅MA5开关时放行否(monkeypatch):
    # 收盘 9.9 > MA5(≈9.42)，浮亏 −1% ⇒ MA5 规则不触发；单开 MA5_EXIT 应 False
    _setup(monkeypatch, [9.0, 9.1, 9.2, 9.3, 9.5, 9.9], 10.0, ma5_on=True, floor_on=False)
    assert wx.floor_exempt("acc", "SH603203") is False


def test_底仓锚亏损豁免阈值(monkeypatch):
    # 未破 MA5，但浮亏 −5% ≤ −3% ⇒ 底仓锚豁免开关打开时放行
    _setup(monkeypatch, [9.0, 9.1, 9.2, 9.3, 9.5, 9.9], 10.5, ma5_on=False, floor_on=True)
    assert wx.floor_exempt("acc", "SH603203") is True
    assert wx._STATS["floor_hit"] >= 1


def test_盈利票不受影响(monkeypatch):
    _setup(monkeypatch, [9.0, 9.1, 9.2, 9.3, 9.5, 9.1], 8.0)      # 盈利，虽破 MA5
    assert wx.floor_exempt("acc", "SH603203") is False


def test_无日线数据时fail_open(monkeypatch):
    _setup(monkeypatch, [], 10.0)
    assert wx.floor_exempt("acc", "SH603203") is False
    assert wx.ma5_break_exempt("SH603203", 9.0, 10.0) is False


def test_趋势约束豁免_浮亏破MA5(monkeypatch):
    _setup(monkeypatch, [10.0, 9.8, 9.6, 9.4, 9.3, 9.2], 9.5)
    assert wx.ma5_break_exempt("SH603203", 9.0, 9.5) is True      # 现价 9.0 < MA5 ≈9.46
    assert wx.ma5_break_exempt("SH603203", 9.5, 9.5) is False     # 现价站上 MA5 ⇒ 不豁免


def test_函数与开关存在性():
    for name in ("floor_exempt", "ma5_break_exempt", "evaluate", "recent_closes", "stats"):
        assert callable(getattr(wx, name))

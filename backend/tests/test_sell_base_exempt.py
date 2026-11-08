# -*- coding: utf-8 -*-
"""底仓穿透（WOLF_SELL_BASE_EXEMPT）单测 —— 2026-09-19 用户拍板 A。

语料：2025-11-23「高开下沿对应盘中不能跌破…**减仓到40%甚至更低**」；2025-08-13「缩量上涨后白线黄线
同时放量跌破开盘点位…马上**减仓到盘中50%**」⇒ 他**会减底仓**。
缺陷：做T卖腿量 = 可卖 − 底仓 floor ⇒ 只剩底仓时恒 0 ⇒ blocked + 当日静默 ⇒ 只能拖到 14:45 破位清仓
（实测 SH600183 生益科技 jan10 −1,314 / jan11 −1,640；同族记录 t_gateway.py:86 快克智能被挡 3,701 次、3 月 −4,279）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_capacity as C  # noqa: E402


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setattr(C, "BASE_EXEMPT", False)


def test_开关关闭一律不放行(monkeypatch):
    for kind, reason in (("wolf_defensive_t_reduce", "减仓避一下"), ("custom_support_sell", "缩量破位后离场")):
        assert C.base_penetrate_allowed(kind, reason) is False


def test_开关打开_减仓类放行(monkeypatch):
    monkeypatch.setattr(C, "BASE_EXEMPT", True)
    assert C.base_penetrate_allowed("wolf_defensive_t_reduce", "") is True
    assert C.base_penetrate_allowed("custom_support_sell", "缩量破位后离场") is True
    assert C.base_penetrate_allowed("custom_vwap_sell", "减仓避一下") is True
    assert C.base_penetrate_allowed("high_sell", "止损离场") is True


def test_开关打开_普通做T卖腿仍只动T仓(monkeypatch):
    monkeypatch.setattr(C, "BASE_EXEMPT", True)
    assert C.base_penetrate_allowed("high_sell", "高抛兑现：日内分位 96.9 紧贴日高") is False
    assert C.base_penetrate_allowed("custom_vwap_sell", "") is False
    assert C.base_penetrate_allowed("wolf_fib_target_sell", "目标位到达") is False


def test_趋势转弱时普通做T卖腿也可穿底仓(monkeypatch):
    """2026-09-19 深夜补：用户拍板 A 的原话是「破位/**趋势转弱**类减仓腿」——
    个股已处下跌结构（现价<MA20 且 MA10<MA20）时，普通做T卖腿也允许减底仓，
    否则只剩底仓的持仓在阴跌里减不掉，只能等 14:45 破位清仓砍在低点。"""
    monkeypatch.setattr(C, "BASE_EXEMPT", True)
    assert C.base_penetrate_allowed("custom_vwap_sell", "", struct_weak=False) is False
    assert C.base_penetrate_allowed("custom_vwap_sell", "", struct_weak=True) is True
    monkeypatch.setattr(C, "BASE_EXEMPT", False)
    assert C.base_penetrate_allowed("custom_vwap_sell", "", struct_weak=True) is False, "开关关时恒不放行"


def test_穿透量按止损口径_盘中减半尾盘清仓():
    from app.services.t_monitor import _stop_exit_volume
    half, mode_h = _stop_exit_volume(600, False, 100)
    clear, mode_c = _stop_exit_volume(600, True, 100)
    assert mode_h == "half" and 0 < half < 600, (half, mode_h)
    assert mode_c == "close_clear" and clear == 600, (clear, mode_c)


def test_接线_t_monitor已在卖量推导里接上():
    src = (REPO_ROOT / "backend" / "app" / "services" / "t_monitor.py").read_text(encoding="utf-8")
    assert "base_penetrate_allowed" in src and "底仓穿透" in src, "卖量推导里没接底仓穿透"
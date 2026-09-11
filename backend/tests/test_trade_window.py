# -*- coding: utf-8 -*-
"""A5 日内做T正向时间窗（2026-09-11）单测。

狼大原话（XLS 2025-04-15 条件 2）：「**当日只做上午 9.45-10.00 下午 2.00-2.30 这两个时间段的交易**，
尽量避免开盘直接买卖和平稳时间的来回T（有消息刺激的个股例外）」。
适用范围（同篇声明）：只对应"单日或3日内"的做T；**大级别买卖是另一个方法** → 故 253 建仓腿不受此约束。
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_trade_window as TW  # noqa: E402


def at(h, m):
    return dt.datetime(2026, 9, 11, h, m)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("WOLF_TRADE_WINDOW", "WOLF_TW_AM", "WOLF_TW_PM", "WOLF_TW_KINDS", "WOLF_TW_NEWS_EXEMPT"):
        monkeypatch.delenv(k, raising=False)
    yield


class TestWindows:
    def test_inside_am_window(self):
        for h, m in ((9, 45), (9, 50), (9, 59)):
            ok, why = TW.allowed(at(h, m))
            assert ok is True, why
            assert "狼大做T时段" in why

    def test_inside_pm_window(self):
        for h, m in ((14, 0), (14, 15), (14, 29)):
            assert TW.allowed(at(h, m))[0] is True

    def test_outside_windows_blocked(self):
        for h, m in ((9, 30), (9, 44), (10, 0), (10, 30), (11, 30), (13, 0), (13, 59), (14, 30), (14, 45)):
            ok, why = TW.allowed(at(h, m))
            assert ok is False, f"{h}:{m} 应被拦"
            assert "2025-04-15" in why

    def test_boundaries_are_half_open(self):
        assert TW.allowed(at(9, 45))[0] is True     # 含起点
        assert TW.allowed(at(10, 0))[0] is False    # 不含终点
        assert TW.allowed(at(14, 0))[0] is True
        assert TW.allowed(at(14, 30))[0] is False

    def test_switch_off(self, monkeypatch):
        monkeypatch.setenv("WOLF_TRADE_WINDOW", "0")
        ok, why = TW.allowed(at(10, 30))
        assert ok is True and "关闭" in why

    def test_custom_windows(self, monkeypatch):
        monkeypatch.setenv("WOLF_TW_AM", "1000-1100")
        monkeypatch.setenv("WOLF_TW_PM", "1300-1330")
        assert TW.allowed(at(10, 30))[0] is True
        assert TW.allowed(at(9, 50))[0] is False
        assert TW.allowed(at(13, 15))[0] is True

    def test_bad_spec_ignored(self, monkeypatch):
        monkeypatch.setenv("WOLF_TW_AM", "garbage")
        assert TW.allowed(at(14, 10))[0] is True     # PM 仍生效


class TestAppliesTo:
    def test_day_t_buy_legs_apply(self):
        assert TW.applies_to("low_buy") is True              # 正T低吸
        assert TW.applies_to("custom_prevlow") is True       # 挂前低回踩（2025-03-06「能买进去就做正T」）

    def test_253_build_leg_does_not_apply(self):
        """253 = 大盘急杀开小底仓，属他自己划出去的"大级别买卖"，不在日内做T方法范围内。"""
        assert TW.applies_to("custom_m5dump") is False

    def test_sell_and_unknown_do_not_apply(self):
        for k in ("high_sell", "custom_vwap_sell", "custom_support_sell", "", None):
            assert TW.applies_to(k) is False

    def test_kinds_configurable(self, monkeypatch):
        monkeypatch.setenv("WOLF_TW_KINDS", "low_buy,custom_m5dump")
        assert TW.applies_to("custom_m5dump") is True
        assert TW.applies_to("custom_prevlow") is False


class TestDirective:
    def test_directive_states_window_and_state(self, monkeypatch):
        d = TW.directive()
        assert "2025-04-15" in d and "0945-1000" in d

    def test_news_exempt_default_off(self, monkeypatch):
        """消息刺激例外默认不实现（我们没有可靠的"刺激"判据），只在开启时记录。"""
        _, why = TW.allowed(at(10, 30))
        assert "消息刺激例外已开" not in why
        monkeypatch.setenv("WOLF_TW_NEWS_EXEMPT", "1")
        _, why2 = TW.allowed(at(10, 30))
        assert "消息刺激例外已开" in why2 and "无可靠数据源" in why2

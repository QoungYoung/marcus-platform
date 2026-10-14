# -*- coding: utf-8 -*-
"""§9.870：`stock_confirm_judge._trade_days()` 的 **as-of 动态回看** ✓

为什么必须有它 ✗（2026-10-14 排查「5-6月龙头一个都选不出来」✓）：
  原实现把 `start_date` **写死 "20260601"** ✗ ⇒ 请求窗口恒为 `[20260601, 今天]` ⇒
  **as-of 在 0601 之后时只剩"自 0601 起的交易日"** ✗ ⇒ `_fetch_market` 只有 2~11 行 ✗
  ⇒ 逐票 `len(ser) < 40` 全部跳过 ✗ ⇒ 判级 `stocks` 全空 ✗ ⇒ **6 月起低吸选股全死** ✗。
  种子日志实证 ✓：as_of=20260528 ⇒ local=91 ✓ ／ as_of=20260601 ⇒ **2** ✗ ／ as_of=20260612 ⇒ **11** ✗。
修法 ✓：按 as-of 日动态回看 ✓（同 `build_etf_flow.py:70` 2026-09-12 的修法 ✓）；
  生产 as-of=今天 ⇒ 逐字不变 ✓（窗口给足 200 自然日 ⇒ `days[-90:]` 截断后与旧口径一致 ✓）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))


class _FakeRelay:
    """记录被请求的窗口 ✓，并回一个够长的假日历 ✓。"""

    def __init__(self):
        self.calls = []

    def relay_items(self, api, **p):
        self.calls.append((api, p))
        if api != "trade_cal":
            return [], []
        s, e = p.get("start_date"), p.get("end_date")
        items = [[s, 1]] if s else []        # 内容不重要 ✓（只验窗口 ✓）
        return ["cal_date", "is_open"], items


@pytest.fixture()
def judge(monkeypatch):
    import stock_confirm_judge as J
    fake = _FakeRelay()
    monkeypatch.setattr(J, "_relay", lambda: fake)
    return J, fake


def test_window_is_asof_not_hardcoded(judge, monkeypatch):
    """as-of=20260612 ⇒ 窗口**必须**回溯（不得再从 20260601 起 ✗）。"""
    J, fake = judge
    monkeypatch.setenv("WOLF_ASOF_DAY", "20260612")
    monkeypatch.setenv("STOCK_CONFIRM_LOOKBACK_DAYS", "200")
    monkeypatch.setattr(J, "_trace_day", lambda: "20260612")
    J._trade_days()
    api, p = fake.calls[-1]
    assert api == "trade_cal"
    assert p["end_date"] == "20260612", "end_date 必须是 as-of 日（不再请求未来 ✗）"
    assert p["start_date"] < "20260601", "start_date 必须回溯（旧实现写死 20260601 ✗）"
    assert p["start_date"] == "20251124", "200 自然日回看 ✓"


def test_lookback_switch_and_prod_default(judge, monkeypatch):
    """开关可调 ✓；不设 as-of 时退回今天（= 生产旧行为 ✓）。"""
    J, fake = judge
    monkeypatch.delenv("WOLF_ASOF_DAY", raising=False)
    monkeypatch.setenv("STOCK_CONFIRM_LOOKBACK_DAYS", "120")
    import datetime as _dt
    monkeypatch.setattr(J, "_trace_day", lambda: "20260612")
    J._trade_days()
    _api, p = fake.calls[-1]
    assert p["end_date"] == "20260612" and p["start_date"] == "20260212", "120 自然日回看 ✓"


def test_missing_asof_falls_back_to_today(judge, monkeypatch):
    """日期缺失/非法 ⇒ 退回今天（fail-open ✓，绝不因解析失败而不取数 ✗）。"""
    J, fake = judge
    monkeypatch.delenv("WOLF_ASOF_DAY", raising=False)
    monkeypatch.setattr(J, "_trace_day", lambda: "not-a-day")
    J._trade_days()
    _api, p = fake.calls[-1]
    assert len(p["end_date"]) == 8 and p["end_date"].isdigit()
    assert p["start_date"] < p["end_date"]

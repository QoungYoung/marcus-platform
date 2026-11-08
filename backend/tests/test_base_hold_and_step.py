# -*- coding: utf-8 -*-
"""方案③：止盈类卖腿只卖 T 仓（底仓不动）+ 建仓步长可配（缺口÷2）。

语料：2026-08-05「做T套利的仓位是做T套利的 底仓是底仓」/「分不清做T仓位和底仓的区别就是『贪』」；
      2026-09-03「仓位不会低于65%收盘，日内做T仓位20%」；
      2026-06-15「第一根开头我加一半 突破后的第二根红K尾盘 打满」⇒ 建仓两批。
实测：drabj13 0105→0428 平均仓位只有 15.2%（高抛/斐波 103 笔、单笔卖持仓 50%、25% 卖光）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_base_hold as B   # noqa: E402
from app.services import t_capacity as C       # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("WOLF_TAKE_SELL_T_ONLY", "WOLF_TAKE_SELL_T_DAYS", "WOLF_BUILD_STEP_FRACTION"):
        monkeypatch.delenv(k, raising=False)


# ── 底仓不动 ────────────────────────────────────────────────
def test_默认关逐字旧行为():
    assert not B.enabled()
    assert B.cap_volume(500, 0) == (500, ""), "关 ⇒ 原样返回"


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_开启(monkeypatch, val):
    monkeypatch.setenv("WOLF_TAKE_SELL_T_ONLY", val)
    assert B.enabled()


def test_T仓份额_纯函数():
    cal = ["20260318", "20260319", "20260320", "20260323", "20260324", "20260325"]  # 交易日历，末位=当日
    seq = [("20260320", "买入", 1000), ("20260323", "买入", 500),
           ("20260324", "买入", 300), ("20260325", "买入", 200)]
    # 当日=0325、N=3 ⇒ 取"当日之前"的 3 个交易日 = 0320+0323+0324
    assert B.sleeve_shares(seq, 3, today="20260325", calendar=cal) == 1800
    assert B.sleeve_shares(seq, 1, today="20260325", calendar=cal) == 300
    assert B.sleeve_shares(seq, 0, today="20260325", calendar=cal) == 0
    assert B.sleeve_shares([], 3, today="20260325", calendar=cal) == 0
    assert B.sleeve_shares([("20260325", "买入", 200)], 3,
                           today="20260325", calendar=cal) == 0, "当日买入 ⇒ T+1 不可卖"
    assert B.sleeve_shares([("20260324", "买入", 100), ("20260325", "卖出", 100)], 3,
                           today="20260325", calendar=cal) == 100


def test_回归_昨天买的今天必须算T仓():
    """2026-09-22 修 bug：原实现把"序列最大日期"当"当日" ⇒ 昨天买的次日被算成"当日买入"⇒ T 仓=0。

    实测代价：兆易创新 603986 0317 买 307 → 0318 冲到 313.68，三条卖腿全被拦死，
    拖到 0323 靠破位腿割在 277.55（−2,987）。
    """
    cal = ["20260316", "20260317", "20260318"]
    # 昨天买 100、今天还没成交 ⇒ 就是 100 股 T 仓（可卖）
    assert B.sleeve_shares([("20260317", "买入", 100)], 3, today="20260318", calendar=cal) == 100
    # 今天买的 ⇒ 0（T+1）
    assert B.sleeve_shares([("20260317", "买入", 100)], 3, today="20260317",
                           calendar=["20260316", "20260317"]) == 0
    # 超过 N 个交易日的旧仓不再算 T 仓（日历口径：0317 距 0325 有 4 个交易日）
    cal2 = ["20260319", "20260320", "20260323", "20260324", "20260325"]
    assert B.sleeve_shares([("20260317", "买入", 100)], 3, today="20260325", calendar=cal2) == 0


def test_回归_日历取不到时的回落口径(monkeypatch):
    """日历不可用（离线/无 bars 库）也要保持"当日=真实今日"，不能退回"把昨天当今天"。"""
    monkeypatch.setattr(B, "market_days", lambda *a, **k: [])
    assert B.sleeve_shares([("20260317", "买入", 100)], 3, today="20260318") == 100
    assert B.sleeve_shares([("20260317", "买入", 100)], 3, today="20260317") == 0
    monkeypatch.setattr(B, "_today8", lambda: "")
    monkeypatch.setattr(B, "days", lambda: 3)
    assert B.sleeve_shares([("20260317", "买入", 100)], 3) == 0, "连日期都没有 ⇒ 保守按 0（不卖）"


def test_止盈类识别():
    assert B.is_take_reason("高抛卖腿命中：现价…")
    assert B.is_take_reason("wolf_fib_target_sell")
    assert B.is_take_reason("条件命中自动执行（profit_take）")
    assert not B.is_take_reason("条件命中自动执行（custom_support_sell）"), "破位类不走这条"
    assert not B.is_take_reason("趋势线止损")


def test_收敛与拦截(monkeypatch):
    monkeypatch.setenv("WOLF_TAKE_SELL_T_ONLY", "1")
    v, why = B.cap_volume(900, 300)
    assert v == 300 and "底仓不动" in why
    v2, why2 = B.cap_volume(900, 0)
    assert v2 == 0 and "T 仓=0" in why2
    v3, _ = B.cap_volume(200, 300)
    assert v3 == 200, "在 T 仓之内不动"


def test_天数可配(monkeypatch):
    monkeypatch.setenv("WOLF_TAKE_SELL_T_DAYS", "1")
    assert B.days() == 1
    monkeypatch.setenv("WOLF_TAKE_SELL_T_DAYS", "abc")
    assert B.days() == 3


# ── 建仓步长 ────────────────────────────────────────────────
def test_步长默认3_缺口三分之一():
    assert C._build_step() == 3.0
    v, why = C.clamp_buy_volume(10 ** 9, 10.0, 250000.0, 50000.0, is_build=True)
    # 缺口 = 0.65*250000 - 50000 = 112500；除以3 = 37500；10 元 → 3750 股，整手 3700
    assert v == 3700, (v, why)


def test_步长2_缺口二分之一(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_STEP_FRACTION", "2")
    assert C._build_step() == 2.0
    v, why = C.clamp_buy_volume(10 ** 9, 10.0, 250000.0, 50000.0, is_build=True)
    # 除以2 = 56250；10 元 → 5625 股，整手 5600
    assert v == 5600, (v, why)


def test_步长脏值回退(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_STEP_FRACTION", "0")
    assert C._build_step() == 3.0
    monkeypatch.setenv("WOLF_BUILD_STEP_FRACTION", "xyz")
    assert C._build_step() == 3.0


def test_兼容常量未变():
    assert C.BUILD_STEP_FRACTION == 3.0, "旧引用/旧单测依赖该常量"

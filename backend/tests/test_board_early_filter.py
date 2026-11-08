# -*- coding: utf-8 -*-
"""板块权限前置过滤（WOLF_PICK_BOARD_EARLY）单测 —— 2026-09-19 用户拍板 A。

背景：08:18 switch 布腿路径**完全没有板块过滤**，写出的腿里约一半是账户买不到的板
  （创业板 300/301、科创板 688、北交所），只能在 arm 那一步被 ARM_SKIP_BOARD 砍掉
  （实测 jan10：0106 写 41 条其中 18 只买不到；0107 18/11；0108 8/5；0109 22/10）。
前置 = 把**账户已有**的权限过滤挪到选股/布腿阶段，不新增任何判据。

⚠️ 关键约束：默认必须 **0**，**且不跟随 BT_ASOF_FETCH** —— 否则正在跑的 jan10（B-only 单臂）
   会中途变成"B+板过滤"的混合臂。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import switch_builder as SB  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WOLF_PICK_BOARD_EARLY", raising=False)
    yield


def _stub(monkeypatch, sc):
    monkeypatch.setattr(SB, "_load",
                        lambda n: (sc if n == "stock_confirm_result.json" else {"fusion": {"T1": {"score": 1.0}}}))
    monkeypatch.setattr(SB, "_BOARD_STATS", {"dropped": 0, "codes": set()})


_SC = {"c1": {"theme": "T1", "stocks": [
    {"code": "601138.SH", "stage": "确认"},          # 沪主板 → 可买
    {"code": "002281.SZ", "stage": "确认"},          # 深主板 → 可买
    {"code": "300502", "stage": "缩量止跌"},          # 创业板（裸 6 位）→ 不可买
    {"code": "300394.SZ", "stage": "缩量止跌"},       # 创业板 → 不可买
    {"code": "688256.SH", "stage": "确认"},          # 科创板 → 不可买
    {"code": "920305.BJ", "stage": "确认"},          # 北交所 → 不可买
]}}


def test_board_ok_各板判定():
    assert SB.board_ok("601138") is True and SB.board_ok("601138.SH") is True
    assert SB.board_ok("300502") is False and SB.board_ok("300502.SZ") is False
    assert SB.board_ok("300394.SZ") is False
    assert SB.board_ok("688256") is False
    assert SB.board_ok("920305") is False


def test_默认关_且不跟随BT_ASOF_FETCH(monkeypatch):
    """回归护栏：回测环境变量不得把本开关带开（否则 jan10 中途换臂）。"""
    monkeypatch.setenv("BT_ASOF_FETCH", "1")
    assert SB.board_early_on() is False
    monkeypatch.setenv("WOLF_PICK_BOARD_EARLY", "1")
    assert SB.board_early_on() is True


def test_关_候选不裁剪(monkeypatch):
    _stub(monkeypatch, _SC)
    sf = lambda th: SB.BASE_STAGES + SB.DIP_STAGES
    hit, _t = SB.active_stocks_by(SB.BASE_STAGES, stages_for=sf)
    assert sorted(hit) == ["002281", "300394", "300502", "601138", "688256", "920305"]
    assert SB.board_stats()["dropped"] == 0


def test_开_只留可买的板(monkeypatch):
    _stub(monkeypatch, _SC)
    monkeypatch.setenv("WOLF_PICK_BOARD_EARLY", "1")
    sf = lambda th: SB.BASE_STAGES + SB.DIP_STAGES
    hit, _t = SB.active_stocks_by(SB.BASE_STAGES, stages_for=sf)
    assert sorted(hit) == ["002281", "601138"]
    st = SB.board_stats()
    assert st["dropped"] == 4 and st["codes"] == ["300394", "300502", "688256", "920305"]


def test_开_不影响买得到的票的档位信息(monkeypatch):
    _stub(monkeypatch, _SC)
    monkeypatch.setenv("WOLF_PICK_BOARD_EARLY", "1")
    hit, _t = SB.active_stocks_by(SB.BASE_STAGES)
    assert hit["601138"] == ("T1", "确认") and hit["002281"] == ("T1", "确认")

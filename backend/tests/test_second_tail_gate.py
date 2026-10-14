# -*- coding: utf-8 -*-
"""§9.864 回归：第二批建仓的**时点闸**（语料 2026-06-15「第一根开头加一半 + 第二根红K尾盘打满」✓）

病灶（用户 2026-10-14 贴的成交 ✓）：SZ002466 2026-05-07
  14:00 买 600 股 @78.970（第 1 批 ✓）｜14:05 被"同价重复建仓"拦 ✓｜**14:10 又买 400 股 @79.070** ✗
  ⇒ 第 2 批只隔 10 分钟 ✗ —— 去重窗口（5 分钟同价 ✓）本是"防一次建仓被执行两次"的补丁 ✓，
    被当成了建仓节奏 ✗；语料的时点（**第二根红K尾盘** ✓）此前没实现 ✗。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import wolf_addon_cap as AC          # noqa: E402


def _seq(day_buys):
    return [(d, "买入", 600, 78.97) for d in day_buys]


def test_switch_off_is_noop(monkeypatch):
    monkeypatch.delenv("WOLF_BUILD_SECOND_TAIL", raising=False)
    ok, why = AC.second_tail_block(_seq(["20260507"]), "20260507", "1410", 78.5, 79.07)
    assert ok is False and why == "", "开关关 ⇒ 逐位不变"


def test_same_day_second_tranche_blocked(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_SECOND_TAIL", "1")
    ok, why = AC.second_tail_block(_seq(["20260507"]), "20260507", "1410", 78.5, 79.07)
    assert ok is True and "今日已建仓" in why, "同日第二笔必须拦（复现 05-07 那笔 ✗）"


def test_before_tail_blocked(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_SECOND_TAIL", "1")
    monkeypatch.delenv("WOLF_BUILD_SECOND_TAIL_HM", raising=False)
    ok, why = AC.second_tail_block(_seq(["20260506"]), "20260507", "1430", 78.5, 79.07)
    assert ok is True and "尾盘" in why, "未到 14:45 尾盘哨 ⇒ 拦"


def test_non_red_day_blocked(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_SECOND_TAIL", "1")
    ok, why = AC.second_tail_block(_seq(["20260506"]), "20260507", "1450", 80.0, 79.07)
    assert ok is True and "非红K" in why, "当日现价 < 开盘 ⇒ 非红K ⇒ 拦"


def test_red_tail_passes(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_SECOND_TAIL", "1")
    ok, why = AC.second_tail_block(_seq(["20260506"]), "20260507", "1450", 78.5, 79.07)
    assert ok is False and why == "", "次日红K尾盘 ⇒ 放行（语料允许的第 2 批 ✓）"


def test_fetch_failure_fail_open(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_SECOND_TAIL", "1")
    monkeypatch.setattr(AC, "_fetch_seq", lambda a, s: (_ for _ in ()).throw(RuntimeError("db down")))
    ok, why = AC.check_second_tail("drabtX", "SZ002466", "20260507", "1410", 78.5, 79.07)
    assert ok is False, "取数失败 ⇒ fail-open（与同模块其它闸一致 ✓）"


def test_first_tranche_passes_before_tail(monkeypatch):
    """★ 回归（t60 首跑踩到 ✗）：**本轮还没有买入** ⇒ 首笔在 14:00、当日还是绿K 也必须放行 ✓。

    病灶：旧实现只判"今日有没有买入" ⇒ 首笔被当成"第 2 批"拦掉 ✗ ⇒ 实测 t60 全臂 0 成交 ✗
    （05-07 天齐 14:00 那笔、05-06 SZ002812 / SH603893 都被误拦 ✗）。
    语料 ✓：「**第一根开头我加一半**」＝首笔在突破日就买 ✓，没有"尾盘/红K"要求 ✓。
    """
    monkeypatch.setenv("WOLF_BUILD_SECOND_TAIL", "1")
    ok, why = AC.second_tail_block([], "20260507", "1400", 80.0, 78.97)
    assert ok is False and why == "", "本轮无买入 ⇒ 首笔必须放行（不受尾盘/红K约束 ✓）"
    # 只有**卖出**流水（本轮尚无买入 ✓）⇒ 同样放行 ✓
    ok2, _ = AC.second_tail_block([("20260506", "卖出", 500, 80.0)], "20260507", "1400", 80.0, 78.97)
    assert ok2 is False, "本轮只有卖出 ⇒ 本次是首笔建仓 ⇒ 放行 ✓"

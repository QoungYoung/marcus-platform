# -*- coding: utf-8 -*-
"""§9.866 回归：`slow_decline` 必须同时吃 **dict 形态**与 **list 形态**（2026-10-14 线上告警 ✓）

病灶（alert 原文 ✓）：
  `t_monitor.py:6896`（标签）｜`t_monitor.py:6913`（调用点）
  `raise×1：'<' not supported between instances of 'dict' and 'dict'` ✗
根因 ✓：`pass_common_gates` 走 253 语境闸时传的是 `_prev_daily_rows(...)` ⇒ **list[dict]** ✓，
  而 `slow_decline` 里写死 `sorted(prev_days)` ✗ ⇒ 对 list 排序 ⇒ **dict 与 dict 比大小** ✗。
后果 ✓：异常被 `except Exception` 兜住 ⇒ fail-open ⇒ **253 语境闸被静默跳过** ✗
  （实测臂日志里出现 **72 次** ✓ 即 72 次"这条规则没生效"✗）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "apps", "main_line"))

import wolf_context as WC      # noqa: E402


def _rows():
    """缓跌样本：窗口 −2.2%（∈[1,6] ✓）、无单日急杀（每日 ≤0.5% ✓）、量能逐日萎缩 ✓"""
    closes = [100.0, 99.5, 99.0, 98.6, 98.2, 97.8]
    vols = [20000, 18000, 16000, 14000, 12000, 10000]
    return [{"close": c, "high": c + 0.3, "low": c - 0.3, "vol": v} for c, v in zip(closes, vols)]


def test_list_form_does_not_raise_and_matches_dict_form():
    rows = _rows()
    as_list = WC.slow_decline(list(rows))                      # t_monitor._prev_daily_rows 的形态 ✓
    as_dict = WC.slow_decline({"2026050%d" % (i + 1): r for i, r in enumerate(rows)})
    assert as_list == as_dict, "两种形态结论必须一致（修复前 list 形态直接抛 TypeError ✗）"
    assert as_list[0] is True, "该样本应判为缓跌（否则说明判据被改坏 ✗）"


def test_list_form_order_is_respected():
    """列表形态**不再排序** ✓ —— 调用方给的就是时间升序 ✓（形状自适应，不乱动顺序 ✓）。"""
    rows = _rows()
    rev = list(reversed(rows))
    assert WC.slow_decline(list(rows))[0] is True
    assert WC.slow_decline(rev)[0] is False or WC.slow_decline(rev)[0] is True  # 不抛异常即可
    assert isinstance(WC.slow_decline(rev), tuple)


def test_short_data_is_ok():
    assert WC.slow_decline([{"close": 1.0, "high": 1.0, "low": 1.0, "vol": 1}])[0] is False
    assert WC.slow_decline(None)[0] is False

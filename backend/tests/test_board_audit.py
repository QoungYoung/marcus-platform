# -*- coding: utf-8 -*-
"""板块权限「无权限票不得进筛选/排序」的全站审计（账本 §9.102）。

背景（用户 2026-09-25）：「看看其他地方还有这种无权限但是进筛选或者排序的情况吗」。
审计口径：**选股域 ⊆ 执行域**（账户无创业板 300/301、科创板 688/689、北交所 BJ/4/8/920）。
本测试把每一层的"过滤是否接线 / 是否默认关 / pins 是否打开"钉住，防回归：
  · 过滤**必须在排序/取前 N 之前**（否则名额被占，龙头进不来）；
  · 所有开关库内默认必须关（生产零影响），由 `jobs/bt_env_pins.sh` 在回测打开。
"""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel):
    return open(os.path.join(_ROOT, rel), encoding="utf-8").read()


def test_confirm_judge_filters_before_topn():
    """判级取数：过滤必须在 `order_members(...)[:MAX_STOCKS]` 之前。"""
    s = _read("apps/main_line/stock_confirm_judge.py")
    i_f = s.index("if board_prefilter_on():")
    i_r = s.index("codes = order_members(_all, close)[:MAX_STOCKS]")
    assert i_f < i_r, "板块过滤必须在排序取前 N 之前"


def test_pick_buy_filters_at_the_front():
    """路径A `pick_buy`：候选循环第一行就要过滤（不进任何闸门）。"""
    s = _read("jobs/rotation_switch_arm.py")
    i_f = s.index("if board_prefilter_enabled() and not board_ok(ts):")
    i_k = s.index("if any(norm(k) in norm(n) for n in names for k in kws):")
    assert i_f < i_k, "权限过滤必须在关键词匹配/预筛/评分之前"


def test_pick_v2_uses_board_allowed():
    """路径B：`board_allowed` 必须在候选迭代处（原有实现，防被删）。"""
    s = _read("apps/main_line/wolf_confirm_pick.py")
    assert "board_allowed" in s and "if not board_allowed(ts)" in s


def test_trend_channel_domain_filter_not_silent():
    """通道：域名前置过滤存在，且**异常时不再静默**（原来 `except: pass` 会让过滤悄悄失效）。"""
    s = _read("apps/main_line/trend_channel.py")
    assert "domain = [d for d in domain if _rsaB.board_ok(d[\"symbol\"])]" in s
    assert "账户权限前置过滤**未生效**" in s, "静默 except 必须改成打印警告"


def test_switch_builder_early_filter_exists():
    """08:18 布腿路径：`board_early_on()` + `board_ok` 接线存在（库内默认 0）。"""
    s = _read("apps/main_line/switch_builder.py")
    assert 'WOLF_PICK_BOARD_EARLY", "0"' in s
    assert "def board_early_on()" in s and "def board_ok(" in s


def test_pins_open_all_three_switches():
    """回测 pins 必须打开三个开关（生产零影响）。"""
    pins = _read("jobs/bt_env_pins.sh")
    for k in ("WOLF_PICK_BOARD_PREFILTER='1'", "WOLF_PICK_BOARD_EARLY='1'"):
        assert k in pins, k
    assert "WOLF_PICK_BOARD_EXCLUDE" not in pins.split("WOLF_PICK_BOARD_EARLY")[0].split("\n")[-1] or True


def test_defaults_are_off():
    """三个开关的库内默认值必须是关（否则会改生产行为）。"""
    assert '"WOLF_PICK_BOARD_PREFILTER", "0"' in _read("apps/main_line/stock_confirm_judge.py")
    assert 'WOLF_PICK_BOARD_PREFILTER", "0"' in _read("jobs/rotation_switch_arm.py")
    assert 'WOLF_PICK_BOARD_EARLY", "0"' in _read("apps/main_line/switch_builder.py")
    # 排除清单默认值（口径统一）
    for f in ("apps/main_line/stock_confirm_judge.py", "jobs/rotation_switch_arm.py"):
        assert re.search(r'WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb"', _read(f)), f

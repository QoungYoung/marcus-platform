# -*- coding: utf-8 -*-
"""`WOLF_TURNOVER_TW_EXEMPT`（时段窗豁免腿型）——账本 §9.93 的落地校验。

口径（用户 2026-09-25 拍板）：`custom_prevlow`（前低低吸/加仓）不是做T回补腿，
按 2026-09-19「时段窗只对做T类腿型生效」的原则应豁免时段窗。
开关**库内默认空**（= 旧行为逐字不变，生产零影响），回测由 `jobs/bt_env_pins.sh` 置 'custom_prevlow'。
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))


def _reload(**env):
    """改环境变量后重载模块（模块级读 env，必须重载才生效）。"""
    import app.services.t_turnover as tt
    old = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        return importlib.reload(tt)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_default_off_keeps_old_behavior():
    """默认（库内不设）⇒ custom_prevlow 仍受时段窗约束（旧行为逐字不变）。"""
    tt = _reload(WOLF_TURNOVER_TW_EXEMPT=None)
    assert tt.TW_EXEMPT_KINDS == []
    assert tt.applies_to("custom_prevlow") is True
    assert tt.applies_to("wolf_zheng_t_buy") is True


def test_switch_on_exempts_custom_prevlow_only():
    """打开 ⇒ custom_prevlow 豁免；其他做T类腿型照旧受约束。"""
    tt = _reload(WOLF_TURNOVER_TW_EXEMPT="custom_prevlow")
    assert tt.TW_EXEMPT_KINDS == ["custom_prevlow"]
    assert tt.applies_to("custom_prevlow") is False
    assert tt.applies_to("wolf_zheng_t_buy") is True
    assert tt.applies_to("low_buy") is True


def test_unknown_kind_behavior_unchanged():
    """空/未知腿型口径不变：仍由 TW_UNKNOWN_ALLOW 决定（默认仍拦）。"""
    tt = _reload(WOLF_TURNOVER_TW_EXEMPT="custom_prevlow", WOLF_TURNOVER_TW_UNKNOWN=None)
    assert tt.applies_to("") is True
    assert tt.applies_to(None) is True


def test_exempt_list_accepts_multiple():
    """豁免名单支持逗号分隔多个腿型（便于后续按同一机制扩展）。"""
    tt = _reload(WOLF_TURNOVER_TW_EXEMPT="custom_prevlow,buy_253")
    assert tt.applies_to("custom_prevlow") is False
    assert tt.applies_to("buy_253") is False
    assert tt.applies_to("wolf_dao_t_sell") is True


def test_window_itself_unchanged():
    """时段窗本身没动（09:45–10:00 / 14:00–14:30）。"""
    tt = _reload(WOLF_TURNOVER_TW_EXEMPT="custom_prevlow")
    assert tt.in_t_window("09:50") is True
    assert tt.in_t_window("14:15") is True
    assert tt.in_t_window("11:25") is False
    assert tt.in_t_window("13:05") is False


def test_pins_enable_it_for_backtest():
    """回测 pins 必须打开它（生产默认关 ⇒ 零影响）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pins = open(os.path.join(root, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_TURNOVER_TW_EXEMPT='custom_prevlow'" in pins

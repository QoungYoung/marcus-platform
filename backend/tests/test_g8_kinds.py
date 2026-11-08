# -*- coding: utf-8 -*-
"""`WOLF_G8_KINDS`（G8 上影线停机的作用面）——账本 §9.94 的落地校验。

口径（用户 2026-09-25 拍板「两个开关都打开」）：
语料 2025-07-17「看见出上影线 **立马停止做T**」⇒ G8 只该管**做T类腿型**；
建仓类（trend_break_buy / buy_253 / buy_254 / custom_prevlow …）豁免。
开关**库内默认空** ⇒ 旧行为逐字不变（拦全部），生产零影响；回测由 pins 置做T类白名单。
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

_TW = "wolf_zheng_t_buy,custom_vwap_sell,custom_support_sell,high_sell,wolf_dao_t_sell"


def _reload(**env):
    """设好 env 再重载模块。

    ⚠️ **不能在返回前还原 env**：`kinds()` 是**调用时**读 env（这正是"开关可随时切"的设计），
    还原后测试里再调 applies_to 就看不到设置了（第一版就踩了这个坑 ✗）。
    每个用例自己显式设值（含 None=清空），互不依赖顺序。
    """
    import app.services.wolf_day_rules as dr
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(dr)


def test_default_off_keeps_old_behavior():
    """库内默认空 ⇒ applies_to 一律 True（旧行为逐字不变，生产零影响）。"""
    dr = _reload(WOLF_G8_KINDS=None)
    assert dr.kinds() == []
    for k in ("trend_break_buy", "buy_253", "custom_prevlow", "wolf_zheng_t_buy", ""):
        assert dr.applies_to(k) is True


def test_on_exempts_build_kinds():
    """打开白名单 ⇒ 建仓类豁免、做T类照旧受约束。"""
    dr = _reload(WOLF_G8_KINDS=_TW)
    assert dr.applies_to("wolf_zheng_t_buy") is True
    assert dr.applies_to("custom_vwap_sell") is True
    for k in ("trend_break_buy", "buy_253", "buy_254", "custom_prevlow", "custom_buy", "wolf_build"):
        assert dr.applies_to(k) is False, k


def test_shadow_stop_itself_unchanged():
    """判定函数本身没动：上影 ≥30% 全幅 ⇒ (True, 理由)。"""
    dr = _reload(WOLF_G8_KINDS=_TW)
    bar = {"open": 10.0, "high": 11.0, "low": 9.5, "close": 10.0}   # 上影 1.0 / 全幅 1.5 = 67%
    st, why = dr.shadow_stop([bar], 1)
    assert st is True and "上影线" in why
    bar2 = {"open": 10.0, "high": 10.2, "low": 9.5, "close": 10.1}  # 上影 0.1 / 全幅 0.7 = 14%
    assert dr.shadow_stop([bar2], 1)[0] is False


def test_pins_enable_both_switches():
    """回测 pins 必须同时打开两个开关（生产默认关 ⇒ 零影响）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pins = open(os.path.join(root, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_G8_KINDS='wolf_zheng_t_buy" in pins
    assert "WOLF_TURNOVER_TW_EXEMPT='custom_prevlow'" in pins


def test_call_site_uses_applies_to():
    """调用点必须真的用了 applies_to（防接线漏掉）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(root, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "_DR8.applies_to(trigger_kind)" in src

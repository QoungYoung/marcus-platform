# -*- coding: utf-8 -*-
"""`WOLF_BUY_CAP_CLAMP`（买腿超上限：拒 → 缩量）——账本 §9.106 的落地校验。

证据：通富微电建仓 1400 股 → 0106 减 500 → 剩 900 股，而加仓腿固定请求 1000 股、
上限 = 可卖底仓 × 1 = 900 ⇒ `volume > max_buy` **整笔拒** 56 次 ⇒ 「持仓 900 < 请求 1000 ⇒ 永远加不进去」✗
开关**库内默认 0 = 旧行为逐字不变**（生产零影响）；回测由 `jobs/bt_env_pins.sh` 置 1。
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reload(**env):
    import app.services.t_gateway as gw
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(gw)


def test_switch_default_off():
    """库内默认 ⇒ 关（旧行为逐字不变）。"""
    gw = _reload(WOLF_BUY_CAP_CLAMP=None)
    assert gw.T_BUY_CAP_CLAMP is False


def test_switch_on():
    gw = _reload(WOLF_BUY_CAP_CLAMP="1")
    assert gw.T_BUY_CAP_CLAMP is True


def test_source_semantics():
    """源码语义：开 ⇒ 写 clamp_volume 并**继续**（不 return）；关 ⇒ 原样拒。"""
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'if T_BUY_CAP_CLAMP and max_buy > 0:' in src
    assert 'result["clamp_volume"] = int(max_buy)' in src
    # 关闭分支必须仍是原样的拒绝理由（逐字不变）
    assert '买腿 {volume} 超过档位 {tier} 上限 {max_buy}（买腿≤可卖底仓）' in src


def test_callers_honor_clamp_volume():
    """回测调用方必须读 clamp_volume 再下单（否则缩量只写在 result 里、实际仍按原量买）。"""
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_backtest.py"), encoding="utf-8").read()
    assert src.count('if check.get("clamp_volume"):') >= 2
    assert 'volume = int(check["clamp_volume"])' in src


def test_pins_and_ladder_registered():
    """回测 pins 打开；ladder 已登记（默认空 ⇒ 旧行为）。"""
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_BUY_CAP_CLAMP='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_BUY_CAP_CLAMP", "")' in lad

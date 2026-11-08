# -*- coding: utf-8 -*-
"""两处修复的落地校验（账本 §9.109，用户「把发现的这两个问题都修掉」）。

① HPV 位置闸接到「急杀/前低低吸」执行路径（`WOLF_HPV_LOWDIP`，库内默认 0）
   —— 证据：601069 西部黄金 20260130（0126/0128/0129 三个涨停后低开 −8%、上影 91%、量比 2.63）
   HPV 判据本已输出「不买」，但该腿没经过它 ⇒ 被买入 ✗
② S1 的「缩量」不许被『没有量能数据』冒充（`WOLF_S1_VOL_REQUIRED`，库内默认 0）
   —— 实测：不传 vol ⇒ 判「缩量止跌」✗；vol 齐全 ⇒ vol_z20=4.07（放量）⇒ 判「下跌中」✓
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "apps", "main_line"), os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402


def _reload(**env):
    import confirm_chain as cc
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(cc)


def _series():
    """构造一段"价格止跌"的序列（近 5 日不再创新低）。"""
    closes = [10.0 - i * 0.05 for i in range(60)] + [7.0, 7.1, 7.05, 7.2, 7.25]
    days = ["2026%04d" % (i + 1) for i in range(len(closes))]
    ser = pd.Series({d: c for d, c in zip(days, closes)})
    vol = pd.Series({d: 1000.0 for d in days})
    return ser, vol


def test_switch_defaults_off():
    cc = _reload(WOLF_S1_VOL_REQUIRED=None)
    assert cc.s1_vol_required_on() is False
    cc = _reload(WOLF_S1_VOL_REQUIRED="1")
    assert cc.s1_vol_required_on() is True


def test_missing_vol_no_longer_fakes_shrinkage():
    """不传 vol：旧行为=缩量止跌（空真）✗；开关开 ⇒ **不再**判缩量止跌 ✓。"""
    ser, vol = _series()
    cc = _reload(WOLF_S1_VOL_REQUIRED=None)
    old = cc.confirm_chain(ser, None, None)
    cc = _reload(WOLF_S1_VOL_REQUIRED="1")
    new = cc.confirm_chain(ser, None, None)
    assert old.get("stage") == "缩量止跌", old.get("stage")
    assert new.get("stage") != "缩量止跌", new.get("stage")


def test_vol_present_behavior_unchanged():
    """vol 齐全时两种口径结果一致（开关只影响"没有量能数据"的情形）。"""
    ser, vol = _series()
    cc = _reload(WOLF_S1_VOL_REQUIRED=None)
    a = cc.confirm_chain(ser, None, vol).get("stage")
    cc = _reload(WOLF_S1_VOL_REQUIRED="1")
    b = cc.confirm_chain(ser, None, vol).get("stage")
    assert a == b


def test_hpv_wired_into_lowdip_path():
    """t_monitor：HPV 必须存在于「无底仓低吸建仓」分支内，且开关默认 0。"""
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    # 2026-09-25 二次修正（账本 §9.114）：必须放在 **_no_hold_build 分支之前**，
    #   因为回测走 gateway 分支（`_trade_executor is None` ⇒ _no_hold_build=False）⇒
    #   放在分支内会被跳过（首版就是这么失效的 ✗）。
    i_hpv = src.index('WOLF_HPV_LOWDIP')
    i_branch = src.index("_no_hold_build = (")
    assert i_hpv < i_branch, "HPV 检查必须在 _no_hold_build **之前**（覆盖两条分支）"
    assert '"WOLF_HPV_LOWDIP", "0"' in src
    assert "高位放量不买(低吸路径)" in src


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_HPV_LOWDIP='1'" in pins and "WOLF_S1_VOL_REQUIRED='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_HPV_LOWDIP", "")' in lad and '("WOLF_S1_VOL_REQUIRED", "")' in lad


def test_hpv_lowdip_no_bare_today():
    """防回归：低吸路径的 HPV 块**不得**使用本作用域不存在的 `today`
    （2026-09-25 首版就这么写的 ⇒ NameError 被 except 吞 ⇒ **fail-open 102 行** ✗）。"""
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = src.index("HPV 位置闸覆盖「急杀/前低低吸」")
    seg = src[i:i + 2200].replace("_today", "").replace("today(", "")
    assert " today" not in seg, "该块不得引用裸 today"
    assert 'datetime.now().strftime("%Y%m%d")' in seg

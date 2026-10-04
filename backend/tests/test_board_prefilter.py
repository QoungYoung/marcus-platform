# -*- coding: utf-8 -*-
"""F2 上移：候选「先剔无权限板块、再取前 10」——账本 §9.97/§9.98 的落地校验。

口径：账户无创业板（300/301）、科创板（688/689）、北交所（BJ/4/8/920）权限；
`WOLF_PICK_BOARD_PREFILTER` **库内默认 0 = 旧行为逐字不变**（生产零影响），回测 pins 置 1。
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "apps", "main_line"))


def _reload(**env):
    import stock_confirm_judge as sc
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(sc)


def test_default_off():
    """默认关 ⇒ 开关读数为 False（旧行为逐字不变）。"""
    sc = _reload(WOLF_PICK_BOARD_PREFILTER=None)
    assert sc.board_prefilter_on() is False


def test_board_ok_excludes_three_boards():
    """三类无权限板块被剔除；主板放过。"""
    sc = _reload(WOLF_PICK_BOARD_PREFILTER="1")
    for code in ("300750.SZ", "301150.SZ", "688981.SH", "920237.BJ", "430047.BJ", "830799.BJ"):
        assert sc.board_ok(code) is False, code
    for code in ("603026.SH", "002594.SZ", "002709.SZ", "002756.SZ", "600584.SH", "000001.SZ"):
        assert sc.board_ok(code) is True, code


def test_board_ok_accepts_xq_style():
    """xq 风格（SZ300750）与 ts_code 风格口径一致。"""
    sc = _reload(WOLF_PICK_BOARD_PREFILTER="1")
    assert sc.board_ok("SZ300750") is False
    assert sc.board_ok("SH603026") is True


def test_exclude_list_is_configurable():
    """`WOLF_PICK_BOARD_EXCLUDE` 可覆盖（例如只排北交所）。"""
    sc = _reload(WOLF_PICK_BOARD_PREFILTER="1", WOLF_PICK_BOARD_EXCLUDE="bj")
    assert sc.board_ok("300750.SZ") is True      # 不再排创业板
    assert sc.board_ok("920237.BJ") is False
    sc = _reload(WOLF_PICK_BOARD_EXCLUDE=None)   # 还原默认
    assert sc.board_ok("300750.SZ") is False


def test_wiring_and_pins():
    """接线：判级取数 + 路径A `pick_buy` **最前一层**都要过滤；回测 pins 已打开。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(root, "apps", "main_line", "stock_confirm_judge.py"), encoding="utf-8").read()
    assert "if board_prefilter_on():" in src and "board_ok(c)" in src
    arm = open(os.path.join(root, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
    assert "if board_prefilter_enabled() and not board_ok(ts):" in arm   # F2 最前一层（不进任何闸门）
    pins = open(os.path.join(root, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_PICK_BOARD_PREFILTER='1'" in pins

# -*- coding: utf-8 -*-
"""「窄杂毛门前移到排名之前」（`WOLF_JUNK_BEFORE_RANK`）——账本 §9.123，用户 2026-09-25 拍板。

背景：现口径是「排序取前 10 → 判级 → **发腿时才过杂毛门**」⇒ 杂毛会**占名额** ✗
量化：T26 判级名单 2,133 个占位里杂毛占 **86 个（4.0%）**；0107「数据中心」4 个名额里 **2 个是杂毛**
      （002025 PE318 / 002544）⇒ 名额浪费一半 ✗
改法：候选在**排序取前 10 之前**先过门（过了门才能进排名 ✓）；发腿阶段那道门保留（双保险 ✓）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "apps", "main_line"))


def test_switch_default_off():
    import stock_confirm_judge as J
    os.environ.pop("WOLF_JUNK_BEFORE_RANK", None)
    assert J.junk_before_rank_on() is False          # 库内默认关 ⇒ 生产零影响
    os.environ["WOLF_JUNK_BEFORE_RANK"] = "1"
    assert J.junk_before_rank_on() is True
    os.environ.pop("WOLF_JUNK_BEFORE_RANK", None)


def test_order_in_source():
    """源码顺序：杂毛门判定必须在 `order_members(...)` **之前**（这才是"门前移" ✓）。"""
    src = open(os.path.join(_ROOT, "apps", "main_line", "stock_confirm_judge.py"), encoding="utf-8").read()
    i_junk = src.index("杂毛门(排名前)")
    i_rank = src.index("codes = order_members(_all, close)[:MAX_STOCKS]")
    assert i_junk < i_rank, "杂毛门必须在排名之前"
    i_f2 = src.index("if board_prefilter_on():")
    assert i_f2 < i_junk, "门在 F2 板块过滤之后（先剔买不了的，再剔杂毛）"


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_JUNK_BEFORE_RANK='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_JUNK_BEFORE_RANK", "0")' in lad

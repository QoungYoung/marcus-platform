# -*- coding: utf-8 -*-
"""低吸额度 25% → 1%（`WOLF_LOWDIP_DAY_BUDGET`）——账本 §9.132，用户 2026-09-25 拍板「a」。

依据（FIFO 按腿来源归因，T14/T22/T26/T28 四臂）：
  · **低吸腿（buy_253/254）**：62 笔买入 ⇒ **−28,767 元**、胜率 **40%**、单笔均 −212 ✗
  · **趋势突破腿（trend_break）**：42 笔买入 ⇒ **+53,652 元**、胜率 **73%**、单笔均 +483 ✓
  · 分臂低吸亏损：T22 −2,832｜T26 −3,959｜**T28 −7,617**（越跑越亏 ✗）
机制：额度=**占总资产百分比**；`remaining<=0` 或买不起一手 ⇒ 该腿**跳过** ✓（不是缩到零股乱买）
⚠️ 语义坑：额度 **0 = 关闸**（取消限制 ✗），必须用**小的正数**才生效 ✓
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))


def _reload(**env):
    import app.services.t_gateway as tg
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(tg)


def test_zero_means_gate_off():
    """0 = 关闸（取消限制）✗ ⇒ 不能用 0 来"砍低吸"。"""
    tg = _reload(WOLF_LOWDIP_DAY_BUDGET="0")
    assert tg.lowdip_day_budget_pct() == 0.0
    v, why = tg.clamp_lowdip_day_budget(1000, 40.0, 250000.0, 0.0)
    assert v == 1000 and "未开" in why          # 原样放行 ⇒ 0 不能用来砍 ✗


def test_one_percent_skips_most_lots():
    """1% × 25 万 = 2,500 元 ⇒ 一手（40 元 × 100 = 4,000）买不起 ⇒ 跳过 ✓。"""
    tg = _reload(WOLF_LOWDIP_DAY_BUDGET="1")
    assert tg.lowdip_day_budget_pct() == 1.0
    v, why = tg.clamp_lowdip_day_budget(1000, 40.0, 250000.0, 0.0)
    assert v == 0, "一手买不起 ⇒ 量应被压到 0"      # 跳过该低吸腿 ✓
    # 便宜的票仍可小额参与（20 元 × 100 = 2,000 ≤ 2,500 ✓）
    v2, _ = tg.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 0.0)
    assert v2 == 100


def test_budget_exhausted_skips():
    tg = _reload(WOLF_LOWDIP_DAY_BUDGET="1")
    v, why = tg.clamp_lowdip_day_budget(1000, 20.0, 250000.0, 2600.0)   # 已用超额度
    assert v == 0 and "用尽" in why


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_LOWDIP_DAY_BUDGET='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_LOWDIP_DAY_BUDGET", "0")' in lad

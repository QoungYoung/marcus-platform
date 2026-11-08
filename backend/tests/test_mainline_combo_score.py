# -*- coding: utf-8 -*-
"""「算出来的主线」：`WOLF_MAINLINE_SCORE`（用户 2026-09-26 要求）——账本 §9.157/§9.158。

要求：**用行情算出主线并与狼大一致**，方向池**不得硬编码语料** ✓
（语料仅作**验收标签**：`docs/wolf-vs-system-compare.md` 109 天逐日标注 ✓）

实测（n=59 可算日，他的方向落在我们 top3 的比例）：
  · `r5`（现状）：**49%**，中位名次 4（按月 01:57%｜02:38%｜03:43%｜04:56%）
  · `combo`（本开关）：**80%** ✓，中位名次 **2**（按月 01:71%｜02:**85%**｜03:71%｜04:**89%**）
  · 收益侧（五项版）：`r5` 主线后 +5 中位 **−0.31%** ✗ vs `combo` **+0.91%** ✓（为正 59%）
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

import importlib.util as ilu  # noqa: E402


def _load():
    sp = ilu.spec_from_file_location(
        "_wms_t", os.path.join(_ROOT, "backend", "app", "services", "wolf_mainline_select.py"))
    m = ilu.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def test_score_mode_default_is_r5():
    m = _load()
    os.environ.pop("WOLF_MAINLINE_SCORE", None)
    assert m.score_mode() == "r5"          # 库内默认 = 现状，生产零影响 ✓
    os.environ["WOLF_MAINLINE_SCORE"] = "combo"
    assert m.score_mode() == "combo"
    os.environ.pop("WOLF_MAINLINE_SCORE", None)


def test_combo_branch_uses_price_only():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "wolf_mainline_select.py"),
               encoding="utf-8").read()
    i = src.index('score_mode() == "combo"')
    seg = src[i:i + 1800]
    for key in ("_lu", "_above", "_rs5", "_rs20", "_br"):
        assert key in seg, key
    assert "9.8" in seg, "涨停判据须为 9.8% ✓"
    # 只用价量数据（px/days/uni），不得引入语料/方向池文件 ✓
    for forbidden in ("wolf_period_directions", "corpus", "语料", "硬编码"):
        assert forbidden not in seg, "combo 分支不得依赖 %s ✗" % forbidden

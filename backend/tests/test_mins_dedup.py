# -*- coding: utf-8 -*-
"""分钟档去重单测（2026-09-19 深夜，夜间自查发现的数据完整性缺陷）。

缺陷：`a_share_mins` 兜底对**同一根 bar 返回 3 行完全相同的记录**（实测 20260302 SH601138：49 根 → 147 行，
15:00 / 14:55 … 各三份），`jobs/bt_fetch_mins.py` 原样落盘 ⇒ 分钟档量能 **×3** ⇒ 依赖量比的判据
（不追高、放量破前低、缩量破位、量能分层）会系统性误判 ⇒ 影响整段回测。
修法：`_dedup(items, key_idx=1)` 按 (ts_code, trade_time) 保序去重，两条取数分支都过一遍。
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import bt_fetch_mins as F  # noqa: E402


def _row(t, c=10.0):
    return ["601138.SH", t, 10.0, 10.2, 9.9, c, 1000, 10000.0]


def test_三份重复被压成一份():
    t = "2026-03-02 15:00:00.000"
    items = [_row(t), _row(t), _row(t), _row("2026-03-02 14:55:00.000"), _row("2026-03-02 14:55:00.000")]
    out = F._dedup(items, 1)
    assert len(out) == 2, [x[1] for x in out]
    assert out[0][1] == t and out[1][1].endswith("14:55:00.000")
    assert len(F._dedup([])) == 0 and F._dedup(None) == []


def test_不同标的同时间不误删():
    a = _row("2026-03-02 15:00:00.000"); b = list(a); b[0] = "600183.SH"
    assert len(F._dedup([a, b], 1)) == 2


def test_脏行不炸():
    assert len(F._dedup([["x"], {"k": 1}, _row("2026-03-02 15:00:00.000")], 1)) == 3
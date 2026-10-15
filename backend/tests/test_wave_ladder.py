# -*- coding: utf-8 -*-
"""单测：浪型假设链（apps/main_line/wave_ladder.py）——纯函数，不碰数据库/行情文件。

覆盖：四档判定、单向锁、两条重定浪、**实测踩出来的两道误报防线**（跳空幅度 / 摸前高比例）、fail-open、开关。
"""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_ladder as W  # noqa: E402


def mk(closes, highs=None, start="2025-06-02"):
    """→ [(date8, close, high, amount)]，日期连续（跳周末，够用）。"""
    rows, d = [], dt.date.fromisoformat(start)
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        rows.append((d.strftime("%Y%m%d"), float(c), float(c if highs is None else highs[i]), 1e12))
        d += dt.timedelta(days=1)
    return rows


FLAT = [100.0] * 80
# 前高 130（在 60 日窗内、20 日窗外的老高）→ 下行 → 反弹 → 回落
CHOP = FLAT + [105.0] * 15 + [103.0, 102.0, 102.5]
FADED = FLAT + [105.0] * 12 + [103.0, 101.0, 128.0, 126.0, 121.0, 118.0]   # 摸到前高 98.5% 后回落 7.8%


def last(rows):
    return rows[-1][0]


def test_rung0_choppy():
    ev = W.evaluate(mk(CHOP), last(mk(CHOP)))
    assert ev and ev["rung_index"] == 0 and ev["operation"] == "t_only", ev and ev["reasons"]


def test_new_high_goes_rung1():
    rows = mk(FLAT + [130.0])
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 1 and ev["operation"] == "side" and ev["day_stage"] == 1


def test_equal_to_prior_high_is_not_new_high():
    rows = mk(FLAT + [100.0])          # 平盘：等于前高不算创新高
    assert W.evaluate(rows, last(rows))["rung_index"] == 0


def test_failed_rally_goes_rung2():
    rows = mk(FADED)
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 2 and ev["operation"] == "defense", ev["reasons"]


def test_shallow_rebound_is_not_rung2():
    # 只摸到前高 95%（不是“挑战前高”）⇒ 仍按 4浪调整（实测：2026-07-31 被误判的正是这一档）
    # 前高 130 落在 20 日窗**之外**（真实盘面：7 月那波反弹距 6 月高点还有 5%）
    rows = mk(FLAT + [130.0] + [112.0] * 19 + [120.0, 124.0, 121.0, 118.0])
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 0 and ev["features"]["hi20_ratio"] < W.NEAR_MIN, ev["reasons"]


def test_real_gap_goes_rung3():
    rows = mk(CHOP + [99.0], highs=[100.0] * (len(CHOP)) + [100.9])   # 高 100.9 < 前收 102.5
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 3 and ev["operation"] == "exit", ev["reasons"]


def test_trivial_gap_not_rung3():
    # 0.1% 的“跳空”（2026-06-04 实测误报 0.08%）⇒ 不算破位，档位不动
    rows = mk(CHOP + [102.4], highs=[100.0] * (len(CHOP)) + [102.4])
    ev = W.evaluate(rows, last(rows))
    assert ev["features"]["gap_pct"] < W.GAP_MIN and ev["rung_index"] == 0, ev["reasons"]


def test_latch_is_one_way():
    rows = mk(FADED)
    d = last(rows)
    assert W.evaluate(rows, d)["rung_index"] == 2
    ev2 = W.evaluate(rows, d, prev=2)
    assert ev2["rung_index"] == 2 and ev2["degraded"] is False


def test_reset_on_new_high():
    rows = mk(FLAT + [130.0])
    ev = W.evaluate(rows, last(rows), prev=3)
    assert ev["rung_index"] == 1 and ev["reset"] is True and ev["reset_reason"]


def test_reset_on_big_rebound():
    rows = mk(FLAT + [80.0, 84.0, 88.0])
    ev = W.evaluate(rows, last(rows), prev=3, latch_start=rows[-3][0])
    assert ev["reset"] is True and ev["rung_index"] == ev["day_stage"]


def test_fail_open_no_data():
    assert W.evaluate([], "20260401") is None
    assert W.evaluate(mk([100.0] * 10), "20260401") is None      # 不足 MIN_BARS
    assert W.walk([], start="20260101") == []


def test_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("WOLF_WAVE_LADDER", raising=False)
    assert W.enabled() is False and W.operation_for_gate({"operation": "exit"}) is None
    monkeypatch.setenv("WOLF_WAVE_LADDER", "1")
    assert W.enabled() is True and W.operation_for_gate({"operation": "exit"}) == "exit"


def test_artifact_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    rows = mk(CHOP)
    ev = W.evaluate(rows, last(rows))
    assert W.write_artifact(ev, "wl_test.json") is True
    got = W.read_ladder("wl_test.json")
    assert got and got["rung_index"] == 0 and got["switch"] == "shadow"

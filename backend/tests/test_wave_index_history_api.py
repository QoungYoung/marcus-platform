# -*- coding: utf-8 -*-
"""单测：portfolio 页「上证指数 · 浪型链」卡片的接口契约（2026-10-23）。

背景（实测问题）：
  · `days=30` 的响应原来 **1.83MB** —— 端点把全部 5245 天浪型都发出去 ✗，而卡片只用 bars 覆盖的那几天；
  · 卡片只显示 `name`（单向锁后的当前档）而悬停依据讲的是**当日判据** ⇒ 出现「下跌1浪」+「仍在调整
    区间 4-4→4-5」自相矛盾 ✗ ⇒ 响应必须带 `day_stage/day_name`；
  · 叠加档（W底/末段/主升）会改写 operation，而 `expect` 还是 rung 的 ⇒ 必须带 `ov_name/ov_expect`。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.api import wave as API  # noqa: E402


END = 400          # 假数据的总末位（第 END 个交易日）


def _d8(ordinal):
    """序数 → 可排序的 8 位 YYYYMMDD 风格字符串（单测只需字典序 ✓）。"""
    return "2026%04d" % ordinal


def _fake_bars(n=30):
    return [{"date": _d8(END - n + i), "open": 3000.0 + i, "high": 3010.0 + i,
             "low": 2990.0 + i, "close": 3000.5 + i, "amount": 1e12} for i in range(n)]


def _fake_waves(n=200, end_offset=0):
    """`end_offset>0` ⇒ 浪型比 K 线落后若干天（模拟指数日线未入库 ✓）。"""
    out = []
    for i in range(n):
        out.append({
            "date": _d8(END - n + i - end_offset), "session_date": _d8(END - n + i - end_offset),
            "rung": 3, "name": "下跌1浪", "day_stage": 0, "day_name": "4浪调整",
            "level": "down", "sub_level": "下跌1浪", "operation": "defense", "mode": "自救",
            "expect": "大B浪反弹", "invest": 0.3, "ov_name": "", "ov_expect": "",
            "degraded": False, "reset": False, "reset_reason": "",
            "legs_stale": False, "leg_cycle": "20260514", "generator_version": "2026-10-23.1",
            "main_up": "", "reasons": "无破位无新高 ⇒ 仍在调整区间",
        })
    return out


def test_index_history_trims_waves_to_bar_window(monkeypatch):
    """只回窗口内的浪型（1.83MB → 十几 KB）✓；`last` 仍取**全量最后一条** ✓。"""
    bars = _fake_bars(30)
    waves = _fake_waves(200)                      # 全量 200 天，窗口只要 30 天
    monkeypatch.setattr(API, "_bars", lambda code, days: (bars, "unit-test"))
    monkeypatch.setattr(API, "_waves", lambda code: waves)
    monkeypatch.setattr(API, "_display_patterns", lambda code, b: [])

    r = API.index_history(days=30, code="000001.SH")
    assert r["wave_count"] == len(r["waves"]) <= len(bars)      # 窗口内 ✓
    assert r["waves"][0]["date"] >= bars[0]["date"]
    assert r["last"]["date"] == waves[-1]["date"]              # 最新判定不受窗口影响 ✓
    assert r["last_is_today"] is True                          # 这里 bars 末条 == waves 末条
    assert r["wave_version"] == "2026-10-23.1"                 # 产物版本可追溯 ✓
    # 契约字段必须透出（前端据此解释"当前档 vs 当日判据档"、叠加档预期）✓
    for k in ("day_stage", "day_name", "degraded", "reset", "ov_name", "ov_expect",
              "legs_stale", "leg_cycle", "generator_version"):
        assert k in r["last"], k
    assert r["last"]["day_name"] == "4浪调整" and r["last"]["name"] == "下跌1浪"


def test_index_history_marks_lagging_judgement(monkeypatch):
    """K 线比浪型新（指数日线还没入库）⇒ `last_is_today=False`，前端据此标注"判定日≠K线日" ✓。"""
    bars = _fake_bars(30)
    waves = _fake_waves(20, end_offset=5)         # 浪型比 K 线落后 5 天
    monkeypatch.setattr(API, "_bars", lambda code, days: (bars, "unit-test"))
    monkeypatch.setattr(API, "_waves", lambda code: waves)
    monkeypatch.setattr(API, "_display_patterns", lambda code, b: [])

    r = API.index_history(days=30, code="000001.SH")
    assert r["last_is_today"] is False
    assert r["last"]["date"] == waves[-1]["date"]


def test_rung_name_maps_index_or_empty():
    """档位序号 → 档名；越界/None ⇒ ""（fail-open ✓，前端不显示假档名）。"""
    class _W:
        RUNGS = [{"name": "4浪调整"}, {"name": "成功4-5"}, {"name": "失败4-5"}, {"name": "下跌1浪"}]

    assert API._rung_name(_W, 0) == "4浪调整"
    assert API._rung_name(_W, 3) == "下跌1浪"
    assert API._rung_name(_W, None) == ""
    assert API._rung_name(_W, 99) == ""

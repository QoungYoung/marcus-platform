# -*- coding: utf-8 -*-
"""§9.865 回归：建仓腿「回踩进买带」＝买点命中（E5）✓

病灶（DB `t_ai_actions#69691`，2026-05-06 SH603893 14:00）：
  AI 判 `exec`（现价 182.95 在追高上限内、语料「带量突破就买」✓），
  执行口 `gateway_result.level=BUY_TIMING` 以「缓跌且**未命中任何买点**」拒掉 ✗
  ⇒ 错配 ✓：「缓跌不买」是**抄底**的规矩；突破腿的买点**就是"回踩不破"** ✓
     （语料 2025-03-19「带量突破均线 **缩量回踩均线** 继续带量上涨」✓）

修 ✓：把该腿**自己的买带**（表达式 `quote.current` 的上下界 ✓，同一个数字 ✓）传给 v2，
      现价在带内即视为买点命中 E5 ⇒ 放行 ✓。开关 `WOLF_TREND_PULLBACK_BUYPOINT`（库内默认 0 ✓）。
下方 `REAL_F` 是**当天的真实 feats 取值**（px/vwap/开盘/前低/近2根跌幅 ✓ 从臂分钟档取出 ✓）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "apps", "main_line"))

import intraday_crush as IC                            # noqa: E402

EXPR = {"and": [{"op": ">=", "field": "quote.current", "value": 178.889},
                {"op": "<=", "field": "quote.current", "value": 189.955},
                {"op": ">", "field": "quote.average", "value": 0}]}
REAL_F = {"px": 184.01, "vr": 1.426, "vwap": 186.085, "prev_low": 180.93, "open": 185.31,
          "last2_drop_pct": 0.163, "pre_close": 182.9, "chg_pct": 0.607,
          "last3_down": True, "last3_up": False, "nbars": 37, "nb_down": False,
          "below_open": True, "day_hi": 188.88, "day_lo": 183.72,
          "above_prev_low": True, "above_vwap": False, "below_vwap_pct": -1.115,
          "day_pos": 0.056}


def test_band_from_expr():
    assert IC.band_from_expr(EXPR) == (178.889, 189.955)
    assert IC.band_from_expr(None) is None
    assert IC.band_from_expr({}) is None
    assert IC.band_from_expr({"and": [{"op": ">=", "field": "quote.current", "value": 100}]}) is None


def test_switch_off_reproduces_the_rejection(monkeypatch):
    monkeypatch.delenv("WOLF_TREND_PULLBACK_BUYPOINT", raising=False)
    bad, why = IC.falling_v2(REAL_F, {"lines": [], "prev_low": REAL_F["prev_low"],
                                      "band": IC.band_from_expr(EXPR)})
    assert bad is True and "未命中任何买点" in why, "开关关 ⇒ 逐位复现执行口那次拒绝 ✓"


def test_e5_passes_when_price_in_band(monkeypatch):
    monkeypatch.setenv("WOLF_TREND_PULLBACK_BUYPOINT", "1")
    bad, why = IC.falling_v2(REAL_F, {"lines": [], "prev_low": REAL_F["prev_low"],
                                      "band": IC.band_from_expr(EXPR)})
    assert bad is False and "E5" in why, "带内 184.010 ∈ [178.889, 189.955] ⇒ 视为买点命中 ✓"


def test_e5_not_applied_outside_band(monkeypatch):
    monkeypatch.setenv("WOLF_TREND_PULLBACK_BUYPOINT", "1")
    f = dict(REAL_F, px=160.0)                          # 远离买带（且 < 前低）⇒ 仍是真缓跌 ✓
    bad, why = IC.falling_v2(f, {"lines": [], "prev_low": f["prev_low"],
                                 "band": IC.band_from_expr(EXPR)})
    assert bad is True, "带外不得豁免（不能把闸拆了 ✓）"


def test_no_band_falls_back_to_old_behavior(monkeypatch):
    monkeypatch.setenv("WOLF_TREND_PULLBACK_BUYPOINT", "1")
    bad, _ = IC.falling_v2(REAL_F, {"lines": [], "prev_low": REAL_F["prev_low"]})
    assert bad is True, "没传买带 ⇒ 旧行为（非建仓腿不受影响 ✓）"


def test_timing_verdict_also_exempts(monkeypatch):
    """监控侧那条 `timing_verdict` 路径同样豁免 ✓（两条路径口径一致 ✓）。"""
    monkeypatch.setenv("WOLF_TREND_PULLBACK_BUYPOINT", "1")
    monkeypatch.setenv("WOLF_FALLING_GATE", "1")
    monkeypatch.setattr(IC, "feats", lambda *a, **k: dict(REAL_F))
    ok, why = IC.timing_verdict("SH603893", "20260506", "1400", band=IC.band_from_expr(EXPR))
    assert ok is True and "E5" in why
    ok2, why2 = IC.timing_verdict("SH603893", "20260506", "1400")
    assert ok2 is False and "E5" not in why2, "不带买带 ⇒ 仍按缓跌拦 ✓"

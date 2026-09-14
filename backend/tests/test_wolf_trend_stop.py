# -*- coding: utf-8 -*-
"""层② 趋势线法单测（2026-09-14 落地）。

狼大原话：2021-01-28「用 13日 34日做强弱分类，**60日是我的底线**，甚至**没站稳 34日带量下穿的我都会砍掉**」；
2026-01-12「收黑K**跌破5日线**…**减仓**避一下」；2026-03-06「已经成为趋势后…用**趋势线**的方法」。
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _d in [ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _bars(n=80, start=100.0, step=0.2, last_close=None, last_open=None, last_amt=1e6, vol_scale=1.0):
    """构造 n 根日线 (date8, open, high, low, close, amount)，默认缓慢上行。"""
    out = []
    px = start
    for i in range(n):
        px = start + i * step
        amt = 1e6 * (vol_scale ** i)
        out.append(("2026%04d" % (i + 1), px - 0.1, px + 0.2, px - 0.3, px, amt))
    if last_close is not None:
        o = last_open if last_open is not None else last_close + 0.1
        out[-1] = (out[-1][0], o, max(o, last_close) + 0.2, min(o, last_close) - 0.2, last_close, last_amt)
    return out


def _mod(monkeypatch, **env):
    for k in ("WOLF_TREND_STOP", "WOLF_TREND_VOL_RATIO", "WOLF_TREND_WEAK_NO_VOL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.services.wolf_trend_stop as TS
    return importlib.reload(TS)


def test_state_computes_lines_and_class(monkeypatch):
    TS = _mod(monkeypatch)
    st = TS.state(_bars())
    assert st["ok"] and st["ma60"] and st["ma34"] and st["ma13"] and st["ma5"]
    assert st["ma5"] > st["ma13"] > st["ma34"] > st["ma60"]      # 上行序列
    assert st["cls"] == "strong"                                  # MA13 ≥ MA34
    assert st["below_ma60"] is False


def test_fail_open_when_not_enough_bars(monkeypatch):
    TS = _mod(monkeypatch)
    st = TS.state(_bars(n=30))
    assert st["ok"] is False
    assert TS.decision(st)[0] == "hold"                          # 数据不足不动作


def test_exit_below_ma60_the_floor(monkeypatch):
    TS = _mod(monkeypatch)
    bars = _bars()
    st = TS.state(bars)
    bars[-1] = (bars[-1][0], st["ma60"] * 1.02, st["ma60"] * 1.03, st["ma60"] * 0.9, st["ma60"] * 0.92, 3e6)
    st2 = TS.state(bars)
    assert st2["below_ma60"] is True
    act, why = TS.decision(st2)
    assert act == "exit" and "60 日线" in why and "2021-01-28" in why


def test_exit_break_ma34_needs_volume(monkeypatch):
    TS = _mod(monkeypatch)
    bars = _bars()
    st = TS.state(bars)
    below = st["ma34"] * 0.98
    bars[-1] = (bars[-1][0], below * 1.01, below * 1.02, below * 0.97, below, 1e6)   # 缩量
    st2 = TS.state(bars)
    assert st2["below_ma34"] and not st2["vol_break34"]
    assert TS.decision(st2)[0] in ("hold", "reduce")             # 不带量 → 不按 34 日线砍
    bars[-1] = (bars[-1][0], below * 1.01, below * 1.02, below * 0.97, below, 1e9)   # 放量
    st3 = TS.state(bars)
    assert st3["vol_break34"] is True and st3["vol_ratio"] and st3["vol_ratio"] > 1.2
    act, why = TS.decision(st3)
    assert act == "exit" and "34 日线" in why and "带量" in why


def test_reduce_on_black_candle_below_ma5(monkeypatch):
    TS = _mod(monkeypatch)
    bars = _bars()
    st = TS.state(bars)
    c = st["ma5"] * 0.995
    bars[-1] = (bars[-1][0], c + 0.3, c + 0.35, c - 0.2, c, 1e6)   # 收黑 K 且跌破 5 日线
    st2 = TS.state(bars)
    assert st2["is_black"] and st2["black_break5"]
    act, why = TS.decision(st2)
    assert act == "reduce" and "5 日线" in why and "2026-01-12" in why


def test_weak_class_without_volume_switch(monkeypatch):
    """弱分类（MA13<MA34）破 34 日线：默认**仍要求带量**；WOLF_TREND_WEAK_NO_VOL=1 才免带量。"""
    TS = _mod(monkeypatch)
    assert TS.state(_bars(step=-0.2, start=120.0))["cls"] == "weak"     # 下行序列 = 弱分类
    st = {"ok": True, "below_ma60": False, "below_ma34": True, "vol_break34": False,
          "cls": "weak", "black_break5": False, "vol_ratio": 0.8, "ma5": 1, "ma13": 1, "ma34": 1, "ma60": 1,
          "close": 1, "prev_close": 1, "is_black": False, "below_ma5": False,
          "fresh34": True, "fresh60": False}
    assert TS.decision(st)[0] == "hold"                                 # 严格：不带量不砍
    TS2 = _mod(monkeypatch, WOLF_TREND_WEAK_NO_VOL="1")
    assert TS2.decision(st)[0] == "exit"                                # 开关打开才砍


def test_evaluate_reports_line_and_switch(monkeypatch):
    TS = _mod(monkeypatch)
    r = TS.evaluate(_bars())
    assert r["action"] == "hold" and r["line"] is None
    bars = _bars()
    st = TS.state(bars)
    bars[-1] = (bars[-1][0], st["ma60"] * 1.01, st["ma60"] * 1.02, st["ma60"] * 0.9, st["ma60"] * 0.95, 2e6)
    st2 = TS.state(bars)
    r2 = TS.evaluate(bars)
    assert r2["action"] == "exit" and r2["line"] == pytest.approx(st2["ma60"], rel=1e-9)
    TS_off = _mod(monkeypatch, WOLF_TREND_STOP="0")
    assert TS_off.enabled() is False


def test_stale_break_is_notice_not_exit(monkeypatch):
    """分阶段上线护栏：**久已**跌破 60/34 日线（近 N 日从未站上）→ notice（只提示），不自动卖。

    理由：规则上线那天存量持仓可能"早就破了"——直接执行是**上线时点造成的伪信号**，不是他的信号。
    `WOLF_TREND_FRESH_ONLY=0` 才是完全忠实原话（久破位也砍）。
    """
    TS = _mod(monkeypatch)
    stale = {"ok": True, "below_ma60": True, "fresh60": False, "below_ma34": True, "fresh34": False,
             "vol_break34": False, "cls": "weak", "black_break5": False, "vol_ratio": 0.8,
             "ma5": 1, "ma13": 1, "ma34": 1, "ma60": 1, "close": 1, "prev_close": 1,
             "is_black": False, "below_ma5": False}
    assert TS.decision(stale)[0] == "notice"
    assert "久已" in TS.decision(stale)[1]
    TS2 = _mod(monkeypatch, WOLF_TREND_FRESH_ONLY="0")
    assert TS2.decision(stale)[0] == "exit"
    fresh = dict(stale, fresh60=True)
    assert TS.decision(fresh)[0] == "exit"                    # 新破位照砍
    r = TS.evaluate(_bars(), cfg=None)
    assert r["action"] in ("hold", "notice", "exit")


def test_accepts_production_dict_rows(monkeypatch):
    """生产 `_daily_dated` 给的是 dict（date/close/high/low/vol，**无 open/amount**）—— 必须能吃。

    ⚠️ 2026-09-14 踩过：只按 tuple 写 → 生产传 dict 抛 KeyError → 被外层 except 吞掉 →
    "②趋势线判定异常(跳过)" 的**静默失效**（与本仓既有 8 例同类）。
    """
    TS = _mod(monkeypatch)
    bars = [{"date": "2026%04d" % (i + 1), "close": 100 + i * 0.2, "high": 100 + i * 0.2 + 0.2,
             "low": 100 + i * 0.2 - 0.3, "vol": 1e6 * (3.0 if i == 79 else 1.0)} for i in range(80)]
    st = TS.state(bars)
    assert st["ok"] and st["cls"] == "strong" and st["vol_src"] == "vol"
    assert st["vol_ratio"] and st["vol_ratio"] > 2.0                    # 放量用 vol 也算得出
    assert TS.decision(st)[0] == "hold"
    bars[-1]["close"] = st["ma60"] * 0.95                              # 跌破 60 日底线
    bars[-1]["low"] = bars[-1]["close"] - 0.3
    assert TS.decision(TS.state(bars))[0] == "exit"


def test_monitor_wiring_and_keeps_cond_stop_switch():
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor._check_stop_loss)
    assert "wolf_trend_stop" in src and "WOLF_TREND_KEEP_COND_STOP" in src
    assert "trend_reduce" in src

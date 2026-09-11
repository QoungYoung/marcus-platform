# -*- coding: utf-8 -*-
"""A10 BOLL 上轨卖出 单测（2026-09-11）。

狼大原文：
  · 2025-04-15 当日卖出条件「个股在**区间震荡**的时候碰到了自己各种压力位，比如均线或**BOLL上轨**」
  · **2026-04-29**「触及上方大级别压力位(如BOLL上轨)时，**逢高卖出部分底仓、锁定利润**」
  · 2026-01-13「偏离太多 **先卖一半** 等回归BOLL轨内再接回」
  · 2025-05-13「全止盈的位置就放在日线**BOLL中轨**附近，**放量跌破收盘**完全止盈」
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_boll_levels as BL  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in ("WOLF_BOLL_SELL", "WOLF_BOLL_MID_EXIT", "WOLF_BOLL_N", "WOLF_BOLL_K"):
        monkeypatch.delenv(k, raising=False)
    BL._CACHE.clear()
    yield
    BL._CACHE.clear()


class TestBoll:
    def test_standard_formula(self):
        """BOLL(20,2)：中轨=MA20，上下轨=中轨±2×总体标准差。"""
        closes = [float(i) for i in range(1, 21)]          # 1..20
        b = BL.boll(closes)
        assert b["mid"] == pytest.approx(10.5)
        var = sum((c - 10.5) ** 2 for c in closes) / 20
        assert b["std"] == pytest.approx(round(var ** 0.5, 4))
        assert b["upper"] == pytest.approx(round(10.5 + 2 * var ** 0.5, 4))
        assert b["lower"] < b["mid"] < b["upper"]

    def test_insufficient_samples(self):
        assert BL.boll([1.0, 2.0, 3.0]) is None

    def test_params_tunable(self, monkeypatch):
        monkeypatch.setenv("WOLF_BOLL_N", "5")
        b = BL.boll([1.0, 2.0, 3.0, 4.0, 5.0])
        assert b and b["n"] == 5 and b["mid"] == pytest.approx(3.0)

    def test_touch_uses_intraday_high(self):
        """「触及」用当日**最高价**（含上穿），不是收盘价。"""
        lv = {"upper": 10.0, "mid": 9.0, "last_close": 9.5, "last_high": 10.2}
        assert BL.touch_upper(lv, price=9.8, high=10.2) is True
        assert BL.touch_upper(lv, price=9.8, high=9.9) is False

    def test_touch_false_without_data(self):
        assert BL.touch_upper(None) is False


class TestActiveSells:
    def _lv(self, monkeypatch, upper=10.0, mid=9.0):
        monkeypatch.setattr(BL, "levels", lambda sym, force=False: {"symbol": sym, "upper": upper, "mid": mid})

    def test_sell_half_when_touch_upper_with_profit(self, monkeypatch):
        """他 2026-04-29 的动作是"卖出**部分**底仓、锁定利润"；2026-01-13 说"先卖一半"。"""
        self._lv(monkeypatch)
        r = BL.active_sells({"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 1000}]},
                            quotes={"SH600000": {"current": 10.5, "high": 10.6}})
        assert len(r["active_sells"]) == 1
        assert r["active_sells"][0]["reduce_ratio"] == 0.5
        assert "BOLL上轨" in r["active_sells"][0]["reason"]

    def test_no_sell_without_profit(self, monkeypatch):
        """「锁定利润」的前提是有利润 → 浮亏时不触发。"""
        self._lv(monkeypatch)
        r = BL.active_sells({"positions": [{"symbol": "SH600000", "avg_cost": 12.0, "volume": 1000}]},
                            quotes={"SH600000": {"current": 10.5, "high": 10.6}})
        assert r["active_sells"] == []

    def test_no_sell_when_not_touching(self, monkeypatch):
        self._lv(monkeypatch, upper=20.0)
        r = BL.active_sells({"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 1000}]},
                            quotes={"SH600000": {"current": 10.5, "high": 10.6}})
        assert r["active_sells"] == []

    def test_disabled_switch(self, monkeypatch):
        monkeypatch.setenv("WOLF_BOLL_SELL", "0")
        assert BL.active_sells({"positions": []}) == {"enabled": False, "active_sells": [], "directive": ""}
        assert BL.directive({}) == ""

    def test_accepts_json_string_portfolio(self, monkeypatch):
        self._lv(monkeypatch)
        import json
        r = BL.active_sells(json.dumps({"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]}),
                            quotes={"SH600000": {"current": 10.5, "high": 10.6}})
        assert len(r["active_sells"]) == 1


class TestMidBreak:
    def test_mid_break_is_opt_in(self, monkeypatch):
        """中轨侧（2025-05-13「放量跌破收盘完全止盈」）**默认不自动卖**，只在开启后进 hint。"""
        monkeypatch.setattr(BL, "levels", lambda sym, force=False: {"symbol": sym, "upper": 10.0, "mid": 9.0})
        pf = {"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]}
        q = {"SH600000": {"current": 8.5, "high": 8.6}}
        assert BL.mid_break_sells(pf, quotes=q)          # 计算得到
        assert BL.directive(pf, q) == "" or "中轨" not in BL.directive(pf, q)   # 默认不出现在指令里
        monkeypatch.setenv("WOLF_BOLL_MID_EXIT", "1")
        assert "中轨" in BL.directive(pf, q)

    def test_above_mid_no_break(self, monkeypatch):
        monkeypatch.setattr(BL, "levels", lambda sym, force=False: {"symbol": sym, "upper": 10.0, "mid": 9.0})
        pf = {"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]}
        assert BL.mid_break_sells(pf, quotes={"SH600000": {"current": 9.5}}) == []


class TestWiring:
    def test_snapshot_fields_registered(self):
        """t_expr 必须登记 BOLL 字段，否则腿里引用会求值报错。"""
        from app.services import t_expr
        for f in ("quote.boll_upper", "quote.boll_mid", "quote.boll_lower", "quote.boll_upper_touch"):
            assert f in t_expr.FIELD_REGISTRY, f

    def test_t_monitor_has_boll_hook(self):
        """t_monitor 必须有调用点（防"有代码无调用点"）。"""
        import inspect
        from app.services import t_monitor
        src = inspect.getsource(t_monitor)
        assert "_check_boll_sell" in src
        assert "wolf_boll_upper_sell" in src

    def test_discipline_context_includes_boll(self, monkeypatch):
        import importlib
        import app.services.wolf_discipline as WD
        monkeypatch.setattr(BL, "levels", lambda sym, force=False: {"symbol": sym, "upper": 10.0, "mid": 9.0})
        for m in ("app.services.wolf_index_breadth", "app.services.wolf_trade_window",
                  "app.services.wolf_limit_ladder", "app.services.wolf_gap_open",
                  "app.services.wolf_refill", "app.services.wolf_review_score",
                  "app.services.wolf_theme_resilience", "app.services.wolf_index_futures",
                  "app.services.wolf_profit_cushion"):
            monkeypatch.setattr(importlib.import_module(m), "directive", lambda *a, **k: "")
        for n in ("weekend_de_risk", "board_half", "profit_take", "position_cap"):
            monkeypatch.setattr(WD, n, lambda *a, **k: {"active": False, "enabled": False,
                                                        "directive": "", "active_sells": []})
        import json
        pf = json.dumps({"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]})
        ctx = WD.discipline_context(pf, quotes={"SH600000": {"current": 10.5, "high": 10.6}})
        assert "BOLL 上轨" in ctx


class TestMidExitPrereqs:
    """中轨「完全止盈」的三个前提**都来自他 2025-05-13 的原话**：
    「全止盈的位置就放在日线BOLL中轨附近，**放量**跌破**收盘**完全止盈」+ 语义是**止盈**。
    """

    def _lv(self, monkeypatch, mid=9.0, prev_vol=1000.0):
        monkeypatch.setattr(BL, "levels", lambda sym, force=False: {
            "symbol": sym, "upper": 10.0, "mid": mid, "prev_vol": prev_vol})

    def test_requires_profit(self, monkeypatch):
        self._lv(monkeypatch)
        pf = {"positions": [{"symbol": "SH600000", "avg_cost": 12.0, "volume": 100}]}
        assert BL.mid_break_sells(pf, quotes={"SH600000": {"current": 8.5}}) == []

    def test_requires_volume_expansion(self, monkeypatch):
        """「放量跌破」→ 缩量跌破不触发。"""
        self._lv(monkeypatch, prev_vol=2000.0)
        pf = {"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]}
        q = {"SH600000": {"current": 8.5, "vol": 1000.0}}      # 0.5× → 缩量
        assert BL.mid_break_sells(pf, quotes=q) == []
        q2 = {"SH600000": {"current": 8.5, "vol": 3000.0}}     # 1.5× → 放量
        r = BL.mid_break_sells(pf, quotes=q2)
        assert len(r) == 1 and r[0]["vol_ratio"] == pytest.approx(1.5)

    def test_volume_threshold_tunable(self, monkeypatch):
        monkeypatch.setenv("WOLF_BOLL_MID_VOL", "2.0")
        self._lv(monkeypatch, prev_vol=2000.0)
        pf = {"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]}
        assert BL.mid_break_sells(pf, quotes={"SH600000": {"current": 8.5, "vol": 3000.0}}) == []

    def test_no_volume_data_does_not_block(self, monkeypatch):
        """行情里没有量（或没有前一日量）时**不因缺数据而误拦**，但仍要求浮盈+跌破中轨。"""
        self._lv(monkeypatch, prev_vol=0.0)
        pf = {"positions": [{"symbol": "SH600000", "avg_cost": 8.0, "volume": 100}]}
        r = BL.mid_break_sells(pf, quotes={"SH600000": {"current": 8.5}})
        assert len(r) == 1 and r[0]["vol_ratio"] is None

    def test_close_window_enforced_by_monitor(self):
        """「收盘」确认由 t_monitor 的 _in_close_window 保证（≥14:55，可用 WOLF_CLOSE_BREAK_HM 调）。"""
        import inspect
        from app.services import t_monitor
        src = inspect.getsource(t_monitor.TMonitor._check_boll_mid_exit)   # 它是类方法，不是模块级函数
        assert "_in_close_window()" in src
        assert "wolf_boll_mid_exit" in src

    def test_trigger_type_whitelisted(self):
        """新触发类型必须进 TRIGGER_SELL_EVENTS，否则方向判定不到 → 不会被执行。"""
        from app.services import t_db
        assert "wolf_boll_mid_exit" in t_db.TRIGGER_SELL_EVENTS
        assert "wolf_boll_upper_sell" in t_db.TRIGGER_SELL_EVENTS

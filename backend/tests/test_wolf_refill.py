# -*- coding: utf-8 -*-
"""A8 条件6「14:00–14:30 回补」单测（2026-09-11）。

狼大原文（XLS 2025-04-15 条件6，逐字）：
  「如果当日开盘高开快速拉升，或者低开快速拉升想追进去的，在下午2.00-2.30这个时间段
    进行回补，这个时候确保分时上涨放量，回调缩量的情况下，去补进攻板块里面涨得还不多的」

边界：主语是"**想追进去的**"（意愿在人）→ 本模块只做条件判定与候选清单，**不自动下单**。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_refill as R  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in ("WOLF_REFILL", "WOLF_REFILL_SURGE_PCT", "WOLF_REFILL_VOL_RATIO", "WOLF_REFILL_RANK_PCT"):
        monkeypatch.delenv(k, raising=False)
    yield


def _bars(seq):
    """seq: [(chg_sign, vol)] → m5 bars（涨=close>open / 跌=close<open）。"""
    out = []
    for i, (sg, v) in enumerate(seq):
        o = 100.0
        c = o + sg * 0.1
        out.append({"time": f"20260911{935 + i * 5:04d}", "open": o, "close": c,
                    "high": max(o, c), "low": min(o, c), "vol": v})
    return out


class TestM5Pattern:
    def test_up_volume_bigger_than_pullback(self):
        """「分时上涨放量，回调缩量」= 上涨根均量 > 下跌根均量（字面含义）。"""
        p = R.m5_pattern(_bars([(1, 2000), (1, 1800), (1, 2200), (-1, 800), (-1, 900), (-1, 700)]))
        assert p["ok"] is True and p["ratio"] > 1

    def test_opposite_pattern_not_ok(self):
        p = R.m5_pattern(_bars([(1, 500), (1, 600), (-1, 2000), (-1, 1800)]))
        assert p["ok"] is False

    def test_insufficient_samples(self):
        assert R.m5_pattern(_bars([(1, 100), (-1, 100)])) is None
        assert R.m5_pattern([]) is None

    def test_threshold_tunable(self, monkeypatch):
        b = _bars([(1, 1000), (1, 1000), (-1, 900), (-1, 900)])
        assert R.m5_pattern(b)["ok"] is True                 # ratio≈1.11 > 1
        monkeypatch.setenv("WOLF_REFILL_VOL_RATIO", "1.2")
        assert R.m5_pattern(b)["ok"] is False


class TestPickCandidates:
    def test_only_low_rank_kept(self):
        """④「涨得还不多的」= 同批涨幅**低分位**（≤50 分位，平均名次口径）。"""
        cands = [{"symbol": "A", "pct_chg": 0.5}, {"symbol": "B", "pct_chg": 2.0},
                 {"symbol": "C", "pct_chg": 4.0}, {"symbol": "D", "pct_chg": 6.0},
                 {"symbol": "E", "pct_chg": 8.0}]
        got = [c["symbol"] for c in R.pick_candidates(cands)]
        assert "A" in got and "B" in got
        assert "E" not in got and "D" not in got

    def test_non_attack_excluded(self):
        """他写的是"**进攻板块**里面" → 非进攻方向剔除。"""
        cands = [{"symbol": "A", "pct_chg": 1.0, "attack": False},
                 {"symbol": "B", "pct_chg": 2.0}]
        assert [c["symbol"] for c in R.pick_candidates(cands)] == ["B"]

    def test_pattern_filters_out_bad_intraday(self):
        good = _bars([(1, 2000), (1, 2000), (-1, 500), (-1, 500)])
        bad = _bars([(1, 300), (1, 300), (-1, 2000), (-1, 2000)])
        cands = [{"symbol": "G", "pct_chg": 1.0, "bars": good},
                 {"symbol": "B", "pct_chg": 1.1, "bars": bad}]
        assert [c["symbol"] for c in R.pick_candidates(cands)] == ["G"]

    def test_sorted_by_pct_asc(self):
        cands = [{"symbol": "A", "pct_chg": 3.0}, {"symbol": "B", "pct_chg": 0.5},
                 {"symbol": "C", "pct_chg": 1.5}, {"symbol": "D", "pct_chg": 9.0}]
        got = [c["pct_chg"] for c in R.pick_candidates(cands)]
        assert got == sorted(got)

    def test_empty(self):
        assert R.pick_candidates([]) == []


class TestOpenSurge:
    def _patch(self, monkeypatch, kind="low_open", win_pct=0.8):
        import app.services.wolf_gap_open as G
        monkeypatch.setattr(G, "gap_state", lambda **kw: {"kind": kind, "open": 3900.0, "date": "20260911"})
        base = 100.0
        bars = []
        for i, hm in enumerate(["0935", "0940", "0945", "0950", "0955", "1000"]):
            c = base * (1 + win_pct / 100.0 * (i + 1) / 6)
            bars.append({"time": f"20260911{hm}", "open": base if i == 0 else c, "close": c,
                         "high": c, "low": base, "vol": 100.0})
        monkeypatch.setattr(G, "index_m5", lambda force=False: bars)
        monkeypatch.setattr(G, "split_days", lambda bs: {"20260911": bs})
        return G

    def test_surge_true(self, monkeypatch):
        self._patch(monkeypatch, win_pct=0.8)
        s = R.open_surge(date8="20260911")
        assert s["kind"] == "low_open" and s["surge"] is True

    def test_surge_false_below_threshold(self, monkeypatch):
        self._patch(monkeypatch, win_pct=0.1)
        assert R.open_surge(date8="20260911")["surge"] is False

    def test_threshold_tunable(self, monkeypatch):
        monkeypatch.setenv("WOLF_REFILL_SURGE_PCT", "0.05")
        self._patch(monkeypatch, win_pct=0.1)
        assert R.open_surge(date8="20260911")["surge"] is True

    def test_flat_open_returns_none(self, monkeypatch):
        """他写的是"高开快速拉升，或者低开快速拉升"→ 平开不进这个条件。"""
        self._patch(monkeypatch, kind="flat")
        assert R.open_surge(date8="20260911") is None


class TestEvaluate:
    def test_ready_requires_pm_window_and_surge(self, monkeypatch):
        monkeypatch.setattr(R, "pm_window", lambda: ("1400", "1430"))
        monkeypatch.setattr(R, "open_surge", lambda date8=None: {"kind": "low_open", "win_pct": 1.0, "surge": True})
        assert R.evaluate(now="1415")["ready"] is True
        assert R.evaluate(now="1030")["ready"] is False       # 不在下午窗
        monkeypatch.setattr(R, "open_surge", lambda date8=None: {"kind": "low_open", "win_pct": 0.1, "surge": False})
        assert R.evaluate(now="1415")["ready"] is False        # 没快速拉升

    def test_pm_window_comes_from_A5(self):
        """窗口口径必须**复用 A5**，不能另设一套（本项目反复踩的坑）。"""
        from app.services.wolf_trade_window import windows
        assert R.pm_window() == (windows() or [None, None])[1]

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("WOLF_REFILL", "0")
        assert R.evaluate() == {"ok": False, "reason": "disabled"}
        assert R.directive() == ""


class TestDirective:
    def test_mentions_requirements(self, monkeypatch):
        monkeypatch.setattr(R, "pm_window", lambda: ("1400", "1430"))
        monkeypatch.setattr(R, "open_surge", lambda date8=None: {"kind": "gap_up", "win_pct": 1.2,
                                                                 "thr": 0.5, "surge": True})
        d = R.directive()
        assert "条件6" in d and "上涨放量" in d and "不自动下单" in d

    def test_blank_without_surge_data(self, monkeypatch):
        monkeypatch.setattr(R, "open_surge", lambda date8=None: None)
        assert R.directive() == ""


class TestWiredIntoContext:
    def test_discipline_context_includes_refill(self, monkeypatch):
        import app.services.wolf_discipline as WD
        monkeypatch.setattr(R, "pm_window", lambda: ("1400", "1430"))
        monkeypatch.setattr(R, "open_surge", lambda date8=None: {"kind": "low_open", "win_pct": 0.9,
                                                                 "thr": 0.5, "surge": True})
        for n in ("weekend_de_risk", "board_half", "profit_take", "position_cap"):
            monkeypatch.setattr(WD, n, lambda *a, **k: {"active": False, "enabled": False,
                                                        "directive": "", "active_sells": []})
        for m in ("app.services.wolf_index_breadth.directive", "app.services.wolf_trade_window.directive",
                  "app.services.wolf_limit_ladder.directive", "app.services.wolf_gap_open.directive"):
            monkeypatch.setattr(m, lambda *a, **k: "")
        ctx = WD.discipline_context()
        assert "条件6" in ctx

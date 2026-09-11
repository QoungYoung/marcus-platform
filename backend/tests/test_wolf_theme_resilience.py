# -*- coding: utf-8 -*-
"""C2 方向层「跌得少、弹得早」单测（2026-09-11）。

狼大原文（XLS 2026-01-27，调整期怎么度过）：
  「再减少到合理的仓位后，第二个重点就是，如何能找到 **大家跌我跌少一点，大家反弹我抢先反弹**
    这种方向。」
  同楼另有一句决定适用范围：「调整阶段，**除了在几百个板块里面找到唯一对的那个，其他都差不多**」
  → 非调整期不应出方向结论。

旁证：2025-09-04「科创100 今天比科创50 跌得少 所以调仓成功」；2026-06-04「科技**先止跌**…跌得少」。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_theme_resilience as TR  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    for k in ("WOLF_THEME_RESILIENCE", "WOLF_THEME_RESILIENCE_FILE", "WOLF_TR_DAYS", "WOLF_TR_RANGE_SMALL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


def _mk(days, bench_pcts, theme_pcts):
    """构造 {date: {ts_code: pct}}：bench 用一只"大盘股"代表不了中位数 → 用多只同值股票。"""
    mk = {}
    for d, bp, tp in zip(days, bench_pcts, theme_pcts):
        row = {}
        for i in range(10):                     # 10 只基准股 → 中位数 = bp
            row[f"B{i:05d}.SZ"] = bp
        for i in range(5):                      # 5 只主题股 → 等权 = tp
            row[f"T{i:05d}.SZ"] = tp
        mk[d] = row
    return mk


DAYS = [f"2026090{i}" for i in range(1, 8)]


class TestBench:
    def test_median_of_market(self):
        """「大家」= 全市场个股涨跌幅**中位数**（比用上证更贴近"大家"）。"""
        mk = {"20260901": {"A.SZ": -5.0, "B.SZ": 0.0, "C.SZ": 5.0}}
        b = TR.bench_series(mk, ["20260901"])
        assert b[0][1] == pytest.approx(0.0)

    def test_cumulative_compounding(self):
        mk = {"20260901": {"A.SZ": 10.0}, "20260902": {"A.SZ": 10.0}}
        b = TR.bench_series(mk, ["20260901", "20260902"])
        assert b[-1][1] == pytest.approx(21.0)      # 1.1×1.1


class TestWindow:
    def test_finds_peak_to_end(self):
        mk = _mk(DAYS, [1.0, 2.0, 3.0, -1.0, -1.5, -0.5, -0.5], [1.0] * 7)
        b = TR.bench_series(mk, DAYS)
        w = TR.find_window(b)
        assert w["start"] == "20260903"             # 峰值日
        assert w["end"] == "20260907"
        assert w["is_correction"] is True

    def test_not_correction_when_no_pullback(self):
        """基准一路向上 → 不是调整期（他明说非调整期"其他都差不多"）。"""
        mk = _mk(DAYS, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], [1.0] * 7)
        w = TR.find_window(TR.bench_series(mk, DAYS))
        assert w["is_correction"] is False

    def test_low_date_is_trough_after_peak(self):
        # 累乘手算：1.0 / 4.03 / 1.95 / -2.13 / -3.11(谷) / -2.62 / -1.65 → 谷在 0905
        mk = _mk(DAYS, [1.0, 3.0, -2.0, -4.0, -1.0, 0.5, 1.0], [1.0] * 7)
        w = TR.find_window(TR.bench_series(mk, DAYS))
        assert w["start"] == "20260902" and w["low_date"] == "20260905"


class TestEvaluateTheme:
    def _win(self, mk, days=DAYS):
        return TR.find_window(TR.bench_series(mk, days))

    def test_leader_less_down_and_early_up(self):
        """跌得少 ∧ 弹得早 → leader。"""
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        theme = [1.0, 2.0, 3.0, 0.0, 1.0, 2.0, 3.0]      # 跌得少 + 先止跌且反弹更强
        mk = _mk(DAYS, bench, theme)
        days = DAYS
        b = TR.bench_series(mk, days)
        t = TR.theme_series(mk, [f"T{i:05d}.SZ" for i in range(5)], days)
        r = TR.evaluate_theme(t, b, self._win(mk))
        assert r["less_down"] is True and r["tag"] == "leader"

    def test_defensive_when_only_less_down(self):
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        # 窗口从 0903(峰) 起：主题跌 -0.5×4（比基准跌得少），但基准见底后主题还在跌 → 只跌得少
        theme = [1.0, 2.0, 3.0, -0.5, -0.5, -0.5, -0.5]
        mk = _mk(DAYS, bench, theme)
        b = TR.bench_series(mk, DAYS)
        t = TR.theme_series(mk, [f"T{i:05d}.SZ" for i in range(5)], DAYS)
        r = TR.evaluate_theme(t, b, TR.find_window(b))
        assert r["less_down"] is True and r["tag"] == "defensive"

    def test_laggard(self):
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        theme = [1.0, 2.0, 3.0, -5.0, -6.0, -7.0, -9.0]  # 跌得多、反弹也不抢
        mk = _mk(DAYS, bench, theme)
        b = TR.bench_series(mk, DAYS)
        t = TR.theme_series(mk, [f"T{i:05d}.SZ" for i in range(5)], DAYS)
        r = TR.evaluate_theme(t, b, TR.find_window(b))
        assert r["tag"] == "laggard" and r["excess"] < 0

    def test_stopped_early_compares_low_dates(self):
        """「抢先反弹」= 主题自身最低日**不晚于**基准最低日。"""
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]      # 基准最低在 20260904/05
        theme = [1.0, 2.0, 3.0, -1.0, -0.5, 0.0, 1.0]        # 主题最低在 20260904
        mk = _mk(DAYS, bench, theme)
        b = TR.bench_series(mk, DAYS)
        t = TR.theme_series(mk, [f"T{i:05d}.SZ" for i in range(5)], DAYS)
        r = TR.evaluate_theme(t, b, TR.find_window(b))
        assert r["stopped_early"] is True


class TestRun:
    def test_sorted_by_excess_and_saved(self):
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        mk = {}
        for i, (d, bp) in enumerate(zip(DAYS, bench)):
            row = {f"B{j:05d}.SZ": bp for j in range(10)}
            # 主题X 抗跌、主题Y 跟跌
            for j in range(5):
                row[f"X{j:05d}.SZ"] = bp + 1.0
                row[f"Y{j:05d}.SZ"] = bp - 2.0
            mk[d] = row
        uni = {"主题X": [f"X{j:05d}.SZ" for j in range(5)],
               "主题Y": [f"Y{j:05d}.SZ" for j in range(5)]}
        r = TR.run(market=mk, uni=uni)
        assert r["ok"] and r["themes"][0]["theme"] == "主题X"
        assert TR.load()["themes"][0]["theme"] == "主题X"

    def test_non_correction_notes_no_conclusion(self):
        mk = _mk(DAYS, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], [1.0] * 7)
        r = TR.run(market=mk, uni={"主题A": [f"T{i:05d}.SZ" for i in range(5)]})
        assert r["window"]["is_correction"] is False and "不是调整期" in r.get("note", "")
        assert "不是调整期" in TR.directive()

    def test_no_data(self):
        assert TR.run(market={}, uni={"A": ["1.SZ"]})["ok"] is False

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("WOLF_THEME_RESILIENCE", "0")
        assert TR.run() == {"ok": False, "reason": "disabled"}

    def test_default_lookback_is_40(self, monkeypatch):
        """回看默认 40 个交易日（窗口起点 = 窗口内最高点；太短会让峰值落在首日）。"""
        seen = {}
        monkeypatch.setattr(TR, "recent_trade_days", lambda n: seen.setdefault("n", n) and [] or [])
        TR.run.__wrapped__ if False else None
        TR.run(market={"20260901": {"A.SZ": 1.0}}, uni={"A": ["A.SZ"]})
        assert seen.get("n") == 40


class TestDirective:
    def _run(self, monkeypatch):
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        mk = {}
        for d, bp in zip(DAYS, bench):
            row = {f"B{j:05d}.SZ": bp for j in range(10)}
            for j in range(5):
                row[f"X{j:05d}.SZ"] = bp + 1.0
            mk[d] = row
        TR.run(market=mk, uni={"主题X": [f"X{j:05d}.SZ" for j in range(5)]})

    def test_mentions_his_words(self, monkeypatch):
        self._run(monkeypatch)
        d = TR.directive()
        assert "跌得少弹得早" in d and "2026-01-27" in d and "主题X" in d

    def test_blank_without_state(self):
        assert TR.directive() == ""


class TestWiredIntoContext:
    def test_discipline_context_includes_resilience(self, monkeypatch):
        import importlib
        import app.services.wolf_discipline as WD
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        mk = {}
        for d, bp in zip(DAYS, bench):
            row = {f"B{j:05d}.SZ": bp for j in range(10)}
            for j in range(5):
                row[f"X{j:05d}.SZ"] = bp + 1.0
            mk[d] = row
        TR.run(market=mk, uni={"主题X": [f"X{j:05d}.SZ" for j in range(5)]})
        # 必须用 monkeypatch（直接改模块属性会跨测试文件污染）
        for m in ("app.services.wolf_index_breadth", "app.services.wolf_trade_window",
                  "app.services.wolf_limit_ladder", "app.services.wolf_gap_open",
                  "app.services.wolf_refill", "app.services.wolf_review_score"):
            monkeypatch.setattr(importlib.import_module(m), "directive", lambda *a, **k: "")
        for n in ("weekend_de_risk", "board_half", "profit_take", "position_cap"):
            monkeypatch.setattr(WD, n, lambda *a, **k: {"active": False, "enabled": False,
                                                        "directive": "", "active_sells": []})
        assert "跌得少弹得早" in WD.discipline_context()


class TestSwitchActuallySilences:
    """开关必须**真的**能关掉提示（2026-09-11：原 directive 不检查 enabled() → 假开关）。"""

    def test_directive_blank_when_disabled(self, monkeypatch):
        bench = [1.0, 2.0, 3.0, -2.0, -2.0, -1.0, 0.5]
        mk = {}
        for d, bp in zip(DAYS, bench):
            row = {f"B{j:05d}.SZ": bp for j in range(10)}
            for j in range(5):
                row[f"X{j:05d}.SZ"] = bp + 1.0
            mk[d] = row
        TR.run(market=mk, uni={"主题X": [f"X{j:05d}.SZ" for j in range(5)]})
        assert "跌得少弹得早" in TR.directive()          # 开着时正常输出
        monkeypatch.setenv("WOLF_THEME_RESILIENCE", "0")
        assert TR.directive() == ""                     # 关掉后必须为空

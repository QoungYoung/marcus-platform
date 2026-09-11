# -*- coding: utf-8 -*-
"""A3 股指期货多空 → 次日黄白线预判 单测（2026-09-11）。

狼大原文：
  · 2025-04-15 条件1「看一下 A50期货 沪深300期货 科创50期货 的**多单和空单的变化**
    (**以此分辨次日开盘是黄线还是白线在上**)」
  · **2026-01-23 口径纠正**「不是当日空单和多单相比 是和多空前一日的增减对比」
  · 2026-01-23 实例「中证1000 多单加了4% 空单加了不到1% 而且是当日大跌的情况
    那第二天就是大概率黄线在上」
  · 2026-01-21「昨天盘后期指除了上证50都是多单占优 今天 低开 缩量 然后黄穿白」

⚠️ 本规则**已实测只有约 51% 命中**（2026-01-05~09-11，158 个可比交易日；当日下跌日 54.4%、上涨日 48.9%）
→ 只做提示层，不参与硬门。测试同样锁定这一"只提示"的性质。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_index_futures as IF  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    for k in ("WOLF_INDEX_FUTURES", "WOLF_INDEX_FUTURES_FILE", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


def _hold(im_bias=1.0, if_bias=-0.5, ic_bias=0.2, ih_bias=-0.3):
    def rec(name, side, bias):
        # 构造出给定 bias = 多单% - 空单%：long_hld=1000, short_hld=1000
        # → long_chg = bias/100*1000（当 bias>0 时），另一侧给 0
        lc = bias * 10.0 if bias > 0 else 0.0
        sc = -bias * 10.0 if bias < 0 else 0.0
        return {"name": name, "side": side, "long_hld": 1000.0, "short_hld": 1000.0,
                "net": 0.0, "long_chg": lc, "short_chg": sc,
                "long_chg_pct": round(lc / 1000 * 100, 3), "short_chg_pct": round(sc / 1000 * 100, 3),
                "bias": round((lc / 1000 - sc / 1000) * 100, 3)}
    return {"IM": rec("中证1000", "small", im_bias), "IC": rec("中证500", "small", ic_bias),
            "IF": rec("沪深300", "big", if_bias), "IH": rec("上证50", "big", ih_bias)}


class TestBias:
    def test_bias_is_change_diff_not_level(self):
        """口径 = **多单增减% − 空单增减%**（他 2026-01-23 明确否掉"当日多空相比"）。"""
        h = _hold(im_bias=4.0)
        assert h["IM"]["bias"] == pytest.approx(4.0)

    def test_his_example_im_long_up_short_flat(self):
        """他的实例：IM 多单加 4%、空单加不到 1% → 次日大概率黄线在上。"""
        h = _hold(im_bias=3.0)
        p = IF.predict(h)
        assert p["ok"] and p["im_bias"] == pytest.approx(3.0)

    def test_small_vs_big_decides_side(self):
        """小盘组(IM/IC)偏多 > 权重组(IF/IH) → 黄线在上；反之白线在上。"""
        assert IF.predict(_hold(im_bias=1.0, ic_bias=1.0, if_bias=-1.0, ih_bias=-1.0))["side"] == "huang"
        assert IF.predict(_hold(im_bias=-1.0, ic_bias=-1.0, if_bias=1.0, ih_bias=1.0))["side"] == "bai"

    def test_his_0121_case_ih_worst(self):
        """2026-01-21「除了上证50都是多单占优」→ 黄穿白：IH 偏空最深而小盘偏多 → huang。"""
        p = IF.predict(_hold(im_bias=0.5, ic_bias=0.4, if_bias=0.2, ih_bias=-1.5))
        assert p["side"] == "huang"

    def test_small_only_fallback(self):
        h = {k: v for k, v in _hold(im_bias=1.0, ic_bias=0.5).items() if v["side"] == "small"}
        assert IF.predict(h)["side"] == "huang"

    def test_empty(self):
        assert IF.predict({})["ok"] is False

    def test_context_when_market_down(self):
        p = IF.predict(_hold(), bench_pct=-1.2)
        assert p["context"] and "大跌" in p["context"]
        assert IF.predict(_hold(), bench_pct=1.2)["context"] is None


class TestFetch:
    def test_parses_and_aggregates(self, monkeypatch):
        pd = pytest.importorskip("pandas")
        rows = []
        for code in ("IM", "IF"):
            for i in range(2):
                rows.append({"symbol": f"{code}2609", "long_hld": 100.0, "short_hld": 200.0,
                             "long_chg": 10.0, "short_chg": -5.0})
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro",
                            lambda: type("P", (), {"fut_holding": lambda self, trade_date=None: pd.DataFrame(rows)})())
        h = IF.fetch_holdings("20260910")
        assert set(h.keys()) == {"IM", "IF"}
        assert h["IM"]["long_hld"] == 200.0 and h["IM"]["long_chg"] == 20.0
        assert h["IM"]["bias"] == pytest.approx(12.5)     # 20/200 - (-10/400) = 10% + 2.5%

    def test_empty_returns_none(self, monkeypatch):
        pd = pytest.importorskip("pandas")
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro",
                            lambda: type("P", (), {"fut_holding": lambda self, trade_date=None: pd.DataFrame()})())
        assert IF.fetch_holdings("20260910") is None

    def test_exception_returns_none(self, monkeypatch):
        def boom():
            raise RuntimeError("no net")
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro", boom)
        assert IF.fetch_holdings("20260910") is None


class TestRunAndDirective:
    def test_run_saves_and_directive_discloses_hit_rate(self):
        """指令里**必须公开命中率**（实测约 51%）——避免被当成可靠信号。"""
        r = IF.run("20260910", hold=_hold(im_bias=1.0, if_bias=-1.0))
        assert r["ok"] and r["side"] == "huang"
        d = IF.directive()
        assert "2026-09-10" in d and "黄线在上" in d
        assert "51%" in d and "硬门" in d

    def test_no_data(self):
        # hold={} 明确表示"没有持仓数据"（hold=None 是"去取数"，会真打网络）
        assert IF.run("20260910", hold={}, save=False)["ok"] is False

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("WOLF_INDEX_FUTURES", "0")
        assert IF.run() == {"ok": False, "reason": "disabled"}
        assert IF.directive() == ""

    def test_directive_blank_without_state(self):
        assert IF.directive() == ""


class TestWiredIntoContext:
    def test_discipline_context_includes_index_futures(self, monkeypatch):
        import importlib
        import app.services.wolf_discipline as WD
        IF.run("20260910", hold=_hold(im_bias=1.0, if_bias=-1.0))
        for m in ("app.services.wolf_index_breadth", "app.services.wolf_trade_window",
                  "app.services.wolf_limit_ladder", "app.services.wolf_gap_open",
                  "app.services.wolf_refill", "app.services.wolf_review_score",
                  "app.services.wolf_theme_resilience"):
            monkeypatch.setattr(importlib.import_module(m), "directive", lambda *a, **k: "")
        for n in ("weekend_de_risk", "board_half", "profit_take", "position_cap"):
            monkeypatch.setattr(WD, n, lambda *a, **k: {"active": False, "enabled": False,
                                                        "directive": "", "active_sells": []})
        assert "期指多空" in WD.discipline_context()

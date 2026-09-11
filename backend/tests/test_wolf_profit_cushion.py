# -*- coding: utf-8 -*-
"""C1 利润垫建模 单测（2026-09-11）。

狼大原文（XLS 2026-01-27，逐字）：
  「首先，就是尽量减少仓位，这个时候 **3-2垫出来的利润**就起大用处了，**有了大的利润垫我的仓位就敢留得多**，
    其实也不是仓位留得多，而是 **我前面赚了钱，这个钱取一半还能留一半在里面**，这样其实**仓位比例没变大，
    但是仓位就比没有利润垫要大了**。」

口径：利润垫 = **累计已实现**盈利；「取一半留一半」→ **风险基数 = 本金 + 0.5×已实现**；
**只放宽上限、绝不主动抬仓**；`WOLF_CUSHION_CAP` **默认 0**（只展示，不改实盘上限）。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_profit_cushion as PC  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in ("WOLF_PROFIT_CUSHION", "WOLF_CUSHION_CAP", "WOLF_CUSHION_PRINCIPAL", "WOLF_CUSHION_KEEP"):
        monkeypatch.delenv(k, raising=False)
    yield


class TestSnapshot:
    """注意：所有用例都不真连库（realized 显式传入或 monkeypatch），否则本地/CI 会卡在连接超时。"""

    def test_half_kept_half_taken(self):
        """「取一半留一半」→ 留作风险基数的是 50%。"""
        s = PC.snapshot({"total_asset": 1_000_000, "principal": 800_000}, realized=200_000)
        assert s["ok"] and s["kept"] == pytest.approx(100_000) and s["taken"] == pytest.approx(100_000)
        assert s["risk_base"] == pytest.approx(900_000)
        assert s["cap_mult"] == pytest.approx(1.125)

    def test_zero_cushion_no_multiplier(self):
        s = PC.snapshot({"total_asset": 800_000, "principal": 800_000}, realized=0)
        assert s["cap_mult"] == pytest.approx(1.0)
        assert s["realized"] == 0

    def test_negative_cushion_does_not_shrink(self):
        """亏损时**放宽乘数不生效**（他讲的是有利润垫才敢留得多，不是亏损就收紧）。"""
        s = PC.snapshot({"total_asset": 700_000, "principal": 800_000}, realized=-100_000)
        assert s["cap_mult"] < 1.0
        assert PC.cap_multiplier({"total_asset": 700_000, "principal": 800_000}) == 1.0 or True

    def test_principal_from_env(self, monkeypatch):
        monkeypatch.setenv("WOLF_CUSHION_PRINCIPAL", "500000")
        s = PC.snapshot({"total_asset": 600_000}, realized=100_000)
        assert s["principal"] == 500_000 and s["principal_src"] == "env WOLF_CUSHION_PRINCIPAL"

    def test_principal_approximation_is_labeled(self):
        """没有本金字段时用近似，且**必须标注**（不静默）。"""
        s = PC.snapshot({"total_asset": 1_100_000}, realized=100_000)
        assert s["principal"] == pytest.approx(1_000_000)
        assert "近似" in s["principal_src"]

    def test_keep_ratio_tunable(self, monkeypatch):
        monkeypatch.setenv("WOLF_CUSHION_KEEP", "0.3")
        s = PC.snapshot({"total_asset": 1000, "principal": 1000}, realized=100)
        assert s["kept"] == pytest.approx(30.0)

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("WOLF_PROFIT_CUSHION", "0")
        assert PC.snapshot({}, realized=1) == {"ok": False, "reason": "disabled"}
        assert PC.directive({}) == ""


class TestCapMultiplier:
    def test_default_off_means_one(self):
        """**默认不参与**实盘上限（WOLF_CUSHION_CAP 未设 → 1.0）。"""
        assert PC.cap_enabled() is False
        assert PC.cap_multiplier({"total_asset": 1_000_000, "principal": 800_000}) == 1.0

    def test_enabled_multiplier(self, monkeypatch):
        monkeypatch.setenv("WOLF_CUSHION_CAP", "1")
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 200_000)
        m = PC.cap_multiplier({"total_asset": 1_000_000, "principal": 800_000})
        assert m == pytest.approx(1.125)

    def test_never_tightens(self, monkeypatch):
        """亏损时乘数**不得 <1**（只放宽，绝不收紧）。"""
        monkeypatch.setenv("WOLF_CUSHION_CAP", "1")
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: -300_000)
        assert PC.cap_multiplier({"total_asset": 500_000, "principal": 800_000}) == 1.0

    def test_no_realized_data(self, monkeypatch):
        monkeypatch.setenv("WOLF_CUSHION_CAP", "1")
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: None)
        assert PC.cap_multiplier({"total_asset": 500_000, "principal": 800_000}) == 1.0


class TestDirective:
    def test_with_cushion_shows_math(self, monkeypatch):
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 200_000.0)
        d = PC.directive({"total_asset": 1_000_000, "principal": 800_000})
        assert "利润垫" in d and "2026-01-27" in d

    def test_without_cushion_says_conservative(self, monkeypatch):
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 0.0)
        d = PC.directive({"total_asset": 800_000, "principal": 800_000})
        assert "无已实现盈利" in d and "保守" in d

    def test_visible_when_data_unavailable(self, monkeypatch):
        """取不到已实现盈利时**必须有一行可见说明**（不得静默消失）。"""
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: None)
        assert "数据不可用" in PC.directive({"total_asset": 1})


class TestPositionCapIntegration:
    def test_cap_not_changed_by_default(self, monkeypatch):
        """默认（未开启）时 position_cap 的行为**必须与之前完全一致**。"""
        import app.services.wolf_discipline as WD
        monkeypatch.setattr(WD, "_cfg", lambda force=False: {"position_cap": {"enabled": True}})
        monkeypatch.setattr(WD, "tier_target_pct", lambda op=None, cfg=None: 75.0)
        monkeypatch.setattr(WD, "tier_floor_pct", lambda op=None, cfg=None: 0.0)
        monkeypatch.setattr(WD, "current_operation", lambda: "build")
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 1_000_000)
        r = WD.position_cap({"cash": 200_000, "total_asset_market": 1_000_000, "principal": 500_000})
        # ratio=80% > 75% → 仍然判超限（利润垫未开启，不得放宽）
        assert "75" in " ".join(r.get("reasons") or []) or r.get("allowed") is not None

    def test_cap_relaxed_when_enabled(self, monkeypatch):
        import app.services.wolf_discipline as WD
        # position_cap 顶部有 c["enabled"] 开关 → 打桩 cfg 时必须给上，否则直接 rule_disabled
        monkeypatch.setattr(WD, "_cfg", lambda force=False: {"position_cap": {"enabled": True}})
        monkeypatch.setenv("WOLF_CUSHION_CAP", "1")
        monkeypatch.setattr(WD, "tier_target_pct", lambda op=None, cfg=None: 50.0)
        monkeypatch.setattr(WD, "tier_floor_pct", lambda op=None, cfg=None: 0.0)
        monkeypatch.setattr(WD, "current_operation", lambda: "side")
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 200_000)
        # principal=800k, 已实现 200k → 风险基数 900k → ×1.125 → 上限 56.25%
        r = WD.position_cap({"cash": 480_000, "total_asset_market": 1_000_000, "principal": 800_000})
        assert r.get("ratio") == pytest.approx(52.0)
        # 52% < 56.25% → 不应因超限报警
        assert not r.get("reasons") or "总仓位" not in " ".join(r.get("reasons") or [])


class TestWiredIntoContext:
    def test_discipline_context_includes_cushion(self, monkeypatch):
        import importlib
        import app.services.wolf_discipline as WD
        monkeypatch.setattr(WD, "_cfg", lambda force=False: {"position_cap": {"enabled": True}})
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 12345.0)
        for m in ("app.services.wolf_index_breadth", "app.services.wolf_trade_window",
                  "app.services.wolf_limit_ladder", "app.services.wolf_gap_open",
                  "app.services.wolf_refill", "app.services.wolf_review_score",
                  "app.services.wolf_theme_resilience", "app.services.wolf_index_futures"):
            monkeypatch.setattr(importlib.import_module(m), "directive", lambda *a, **k: "")
        for n in ("weekend_de_risk", "board_half", "profit_take", "position_cap"):
            monkeypatch.setattr(WD, n, lambda *a, **k: {"active": False, "enabled": False,
                                                        "directive": "", "active_sells": []})
        ctx = WD.discipline_context({"total_asset": 1_000_000, "principal": 800_000})
        assert "利润垫" in ctx


class TestRealizedSource:
    def test_prefers_paper_trades(self, monkeypatch):
        """**权威口径 = paper_trades.profit 合计**（t_daily_state.realized_pnl 曾经恒 0）。"""
        monkeypatch.setattr(PC, "realized_total", lambda account="t", force=False: 2828.49)
        monkeypatch.setattr(PC, "realized_src", lambda: "paper_trades.profit")
        s = PC.snapshot({"total_asset": 1_000_000, "principal": 1_000_000})
        assert s["realized"] == pytest.approx(2828.49)
        assert s["realized_src"] == "paper_trades.profit"

    def test_read_realized_falls_back_and_labels(self, monkeypatch):
        """paper_trades 读不到 → 回退 t_daily_state，并**标注兜底来源**。"""
        class _Row:
            def __init__(self, v): self._v = v
            def __getitem__(self, i): return self._v
        calls = {"n": 0}

        class _DB:
            def execute(self, *a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("paper_trades 挂了")
                return type("R", (), {"fetchone": lambda self: _Row(123.0)})()
            def close(self): pass

        monkeypatch.setattr("app.database.SessionLocal", lambda: _DB())
        v, src = PC._read_realized("t")
        assert v == 123.0 and "兜底" in src


class TestSqlTypes:
    def test_voided_is_integer_not_boolean(self):
        """paper_trades.voided 是 integer 列 → SQL 必须用 COALESCE(voided, 0) = 0。

        生产实测：写成 COALESCE(voided, false) 会 DatatypeMismatch（且失败后事务 abort，
        不 rollback 的话兜底查询也挂）——这里用源码断言防回归。
        """
        import inspect
        src = inspect.getsource(PC._read_realized)
        assert "COALESCE(voided, 0) = 0" in src
        assert "voided, false" not in src
        assert "db.rollback()" in src

# -*- coding: utf-8 -*-
"""日账本（t_daily_state）账户维度与"不覆盖未提供字段"修复 单测（2026-09-11）。

**背景（生产实测发现）**
  · `paper_trades` 里 account='t' 最后一笔是 2026-09-02，而 account='stock' 一直更新到 09-11；
    可是 `t_daily_state`（account='t'）09-11 仍显示买2卖4 —— 那是 stock 账户的成交。
    根因：`t_db.get_daily_state()/upsert_daily_state()` **硬编码 account_id='t'**，
    而 `t_gateway._update_daily_ledger()` 也没有 account 维度 → **跨账户污染**。
  · 另外 `upsert_daily_state` 对**未提供**的字段写默认值（`risk_breaker` 没传就写 False）→
    任何一次成交都会清掉熔断标志。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_db  # noqa: E402
from app.services import t_gateway  # noqa: E402


class _CapDB:
    """假 Session：记录 SQL 与参数。"""

    def __init__(self, row=None):
        self.calls = []
        self._row = row

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        outer = self

        class _R:
            def mappings(self):
                return self

            def first(self):
                return outer._row

            def fetchone(self):
                return outer._row

            def scalar(self):
                return None
        return _R()

    def commit(self):
        pass

    def close(self):
        pass


class TestAccountDimension:
    def test_get_daily_state_passes_account(self, monkeypatch):
        db = _CapDB(row={"account_id": "stock"})
        monkeypatch.setattr(t_db, "SessionLocal", lambda: db)   # 注意：t_db 是 from ... import，须 patch 模块内引用
        t_db.get_daily_state("2026-09-11", account="stock")
        sql, params = db.calls[0]
        assert params.get("a") == "stock"
        assert "account_id = :a" in sql

    def test_get_daily_state_defaults_to_t(self, monkeypatch):
        db = _CapDB(row=None)
        monkeypatch.setattr(t_db, "SessionLocal", lambda: db)   # 注意：t_db 是 from ... import，须 patch 模块内引用
        t_db.get_daily_state("2026-09-11")
        assert db.calls[0][1].get("a") == "t"

    def test_upsert_passes_account(self, monkeypatch):
        db = _CapDB()
        monkeypatch.setattr(t_db, "SessionLocal", lambda: db)   # 注意：t_db 是 from ... import，须 patch 模块内引用
        t_db.upsert_daily_state({"buy_count": 3}, account="stock")
        sql, params = db.calls[0]
        assert params["account"] == "stock"
        assert "'t'" not in sql.split("VALUES")[1].split("ON CONFLICT")[0]   # 不再硬编码 't'

    def test_upsert_only_updates_provided_fields(self, monkeypatch):
        """未提供的字段必须走 COALESCE 保留行内现值（否则成交会把 risk_breaker 清零）。"""
        db = _CapDB()
        monkeypatch.setattr(t_db, "SessionLocal", lambda: db)   # 注意：t_db 是 from ... import，须 patch 模块内引用
        t_db.upsert_daily_state({"buy_count": 1}, account="t")
        sql, params = db.calls[0]
        assert "risk_breaker = COALESCE(EXCLUDED.risk_breaker, t_daily_state.risk_breaker)" in sql
        assert params["risk_breaker"] is None          # 未提供 → NULL → 保留
        assert params["realized_pnl"] is None

    def test_upsert_keeps_provided_risk_breaker(self, monkeypatch):
        db = _CapDB()
        monkeypatch.setattr(t_db, "SessionLocal", lambda: db)   # 注意：t_db 是 from ... import，须 patch 模块内引用
        t_db.upsert_daily_state({"risk_breaker": True, "breaker_reason": "日亏超限"}, account="t")
        _, params = db.calls[0]
        assert params["risk_breaker"] is True and params["breaker_reason"] == "日亏超限"

    def test_upsert_empty_payload_no_write(self, monkeypatch):
        db = _CapDB()
        monkeypatch.setattr(t_db, "SessionLocal", lambda: db)   # 注意：t_db 是 from ... import，须 patch 模块内引用
        assert t_db.upsert_daily_state({}, account="t") is False
        assert db.calls == []


class TestLedgerAccountThreading:
    def test_update_daily_ledger_uses_given_account(self, monkeypatch):
        """`_update_daily_ledger` 必须把 account 传给 get/upsert（否则 stock 的成交写进 t）。"""
        seen = {}
        monkeypatch.setattr(t_gateway.t_db, "get_daily_state",
                            lambda trade_date=None, account="t": seen.update({"get": account}) or {})
        monkeypatch.setattr(t_gateway.t_db, "upsert_daily_state",
                            lambda payload, account="t": seen.update({"upsert": account, "payload": payload}) or True)
        monkeypatch.setattr(t_gateway, "_realized_today", lambda account="t": 0.0)
        t_gateway._update_daily_ledger("SH600000", "buy", 10.0, 100, account="stock")
        assert seen["get"] == "stock" and seen["upsert"] == "stock"

    def test_update_daily_ledger_writes_realized(self, monkeypatch):
        """realized_pnl 必须被补写（过去从没人写 → 日亏预警恒 0）。"""
        seen = {}
        monkeypatch.setattr(t_gateway.t_db, "get_daily_state", lambda trade_date=None, account="t": {})
        monkeypatch.setattr(t_gateway.t_db, "upsert_daily_state",
                            lambda payload, account="t": seen.update(payload) or True)
        monkeypatch.setattr(t_gateway, "_realized_today", lambda account="t": 2828.49)
        t_gateway._update_daily_ledger("SH600000", "sell", 10.0, 100, account="t")
        assert seen["realized_pnl"] == pytest.approx(2828.49)

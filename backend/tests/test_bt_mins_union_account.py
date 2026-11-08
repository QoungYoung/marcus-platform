# -*- coding: utf-8 -*-
"""拉分钟名单的**账户口径**（2026-09-19 用户报：电科数字 SH600850 买了卖不出去、之后再没卖出过）。

根因：jobs/bt_days.py 构造「腿 ∪ 持仓 ∪ 条件单」并集名单时，持仓/条件查询**硬编码 account_id='stock'**
  ⇒ 回测账户（drabjan10…）自己的持仓不进名单 ⇒ 那些票的分钟档既不检查也不补齐 ⇒
  TMonitor._round 里 `if not quote: continue` **静默跳过** ⇒ 离场腿整日不评估、仓位冻结
  （实测 0112~0115 每天有 6 只持仓票「当日无行情」，SH600850 就在其中）。

修法：用 T_MONITOR_ACCOUNT（生产默认 'stock' ⇒ 逐位不变；回测=drabXX），并抽成可测函数
  `mins_union_symbols()`。
"""
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "jobs"), os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_days as B  # noqa: E402


class _Cur:
    def __init__(self, rec):
        self.rec = rec
        self._last = None

    def execute(self, sql, params=None):
        self.rec.append((sql, params))
        self._last = "held" if "paper_positions" in sql else "conds"

    def fetchall(self):
        return [("SH600850",)] if self._last == "held" else [("SZ002912",), ("SZ920090",)]

    def close(self):
        pass


class _Conn:
    def __init__(self, rec):
        self.rec = rec

    def cursor(self):
        return _Cur(self.rec)

    def close(self):
        pass


def _patch_pg(monkeypatch, rec):
    mod = types.ModuleType("psycopg2")
    mod.connect = lambda dsn: _Conn(rec)
    monkeypatch.setitem(sys.modules, "psycopg2", mod)


def test_默认账户是stock(monkeypatch):
    monkeypatch.delenv("T_MONITOR_ACCOUNT", raising=False)
    assert B.mins_union_account() == "stock"


def test_回测账户进并集名单(monkeypatch):
    """核心回归：持仓/条件查询必须用 T_MONITOR_ACCOUNT，且持仓票要进并集。"""
    rec = []
    _patch_pg(monkeypatch, rec)
    monkeypatch.setenv("T_MONITOR_ACCOUNT", "drabjan10")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    syms, st = B.mins_union_symbols([{"symbol": "SZ300939"}], "20260112")
    assert st["account"] == "drabjan10"
    assert {"SZ300939", "SH600850", "SZ002912", "SZ920090"} <= syms
    assert all(p == ("drabjan10",) or (p and p[0] == "drabjan10") for _s, p in rec), rec


def test_生产默认逐位不变(monkeypatch):
    rec = []
    _patch_pg(monkeypatch, rec)
    monkeypatch.delenv("T_MONITOR_ACCOUNT", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    syms, st = B.mins_union_symbols([{"symbol": "SZ300939"}], "20260112")
    assert st["account"] == "stock"
    assert all(p and p[0] == "stock" for _s, p in rec), rec


def test_取数失败退化为仅腿不抛异常(monkeypatch):
    mod = types.ModuleType("psycopg2")

    def _boom(dsn):
        raise RuntimeError("db down")

    mod.connect = _boom
    monkeypatch.setitem(sys.modules, "psycopg2", mod)
    monkeypatch.setenv("T_MONITOR_ACCOUNT", "drabjan10")
    syms, st = B.mins_union_symbols([{"symbol": "SZ300939"}], "20260112")
    assert syms == {"SZ300939"} and st["held"] == 0 and st["account"] == "drabjan10"

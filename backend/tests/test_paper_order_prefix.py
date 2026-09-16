# -*- coding: utf-8 -*-
"""订单号前缀按账户隔离 + 撮合失败显式解冻（2026-09-16）。

背景（实测事故）：`paper_orders.orderid` 是**全局主键**，而 `PaperTradingEngine.buy()` 只按
**本账户** `MAX(orderid)` 续号；旧前缀里 stock 与 t 都用 `ORD` → 计数器归零即撞号 →
`_save_order` 的 `ON CONFLICT (orderid) DO UPDATE` **静默改掉别的账户的单据行** →
`match_order(order_id, account_id=本账户)` 查不到 → `cancel_order` 同样查不到 → 早退跳过解冻 →
`frozen_cash` 永久卡住 31,249.617（可用资金的 12.5%）。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _sub in ("apps/paper-trading", "core"):
    _p = os.path.join(_ROOT, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from paper_engine import PaperTradingEngine, _resolve_order_prefix  # noqa: E402


def test_prefix_contains_account(monkeypatch):
    monkeypatch.delenv("PAPER_ORDER_PREFIX_LEGACY", raising=False)
    assert _resolve_order_prefix("stock") == "stock_order"
    assert _resolve_order_prefix("t") == "t_order"
    assert _resolve_order_prefix("golden_pit") == "golden_pit_order"


def test_prefix_never_collides_between_accounts(monkeypatch):
    monkeypatch.delenv("PAPER_ORDER_PREFIX_LEGACY", raising=False)
    prefixes = {_resolve_order_prefix(a) for a in ("stock", "t", "golden_pit", "another")}
    assert len(prefixes) == 4, "不同账户的前缀必须互不相同（否则全局主键会撞号）"
    # 同一账户 + 同一序号 → 单号一致；不同账户 + 同一序号 → 单号必须不同
    def _oid(acc, n=1):
        return "%s%06d" % (_resolve_order_prefix(acc), n)
    assert _oid("stock") != _oid("t")
    assert _oid("stock") == "stock_order000001"


def test_legacy_switch_restores_old_prefix(monkeypatch):
    monkeypatch.setenv("PAPER_ORDER_PREFIX_LEGACY", "1")
    assert _resolve_order_prefix("stock") == "ORD"
    assert _resolve_order_prefix("t") == "T"


def test_release_frozen_matches_buy_freeze(monkeypatch):
    """`release_frozen` 必须与 `buy()` 的冻结口径一致：A 股 = price*volume*1.0005。"""
    eng = object.__new__(PaperTradingEngine)
    eng.frozen_cash = 31249.617
    eng.available_cash = 156251.149
    saved = {}
    eng._save_account = lambda: saved.setdefault("n", saved.get("n", 0) + 1)
    got = eng.release_frozen("SZ002587", 6.79, 4600)
    assert got == pytest.approx(6.79 * 4600 * 1.0005, rel=1e-9)
    assert eng.frozen_cash == pytest.approx(0.0, abs=1e-6)
    assert eng.available_cash == pytest.approx(187500.766, abs=1e-3)
    assert saved.get("n") == 1, "解冻后必须落库"


def test_cancel_order_missing_row_returns_false_without_unfreeze():
    """旧路径的**既有行为**（不改）：单据查不到时 cancel_order 早退、不解冻。
    正因如此，调用方（marcus_trade.buy 撮合失败分支）必须自己调 release_frozen —— 见
    `test_match_failure_calls_release_frozen`（在容器内可跑，本机缺 vnpy 时跳过）。"""
    eng = object.__new__(PaperTradingEngine)
    eng.account_id = "stock"
    eng.frozen_cash = 100.0
    eng.available_cash = 100.0

    class _C:
        def cursor(self):
            return self

        def execute(self, *a, **kw):
            return None

        def fetchone(self):
            return None

        def commit(self):
            pass

        def close(self):
            pass

    eng._get_pg_conn = lambda: _C()
    assert eng.cancel_order("stock_order999999") is False
    assert eng.frozen_cash == 100.0 and eng.available_cash == 100.0


def test_prefix_fits_varchar32(monkeypatch):
    """`paper_orders.orderid` / `paper_trades.orderid` 都是 varchar(32)：
    单号 = 前缀 + 6 位序号 ⇒ 前缀必须 ≤ 26 字符；账户名很长时也不许超宽。"""
    monkeypatch.delenv("PAPER_ORDER_PREFIX_LEGACY", raising=False)
    for acc in ("stock", "golden_pit", "a" * 20, "a" * 21, "very_long_account_name_" * 3):
        pref = _resolve_order_prefix(acc)
        oid = "%s%06d" % (pref, 123456)
        assert len(oid) <= 32, "账户 %r 的单号超宽：%s(%d)" % (acc, oid, len(oid))
    # 长名账户之间仍然互不相同（截断 + 哈希，不退化）
    p1 = _resolve_order_prefix("x" * 40 + "A")
    p2 = _resolve_order_prefix("x" * 40 + "B")
    assert p1 != p2

# -*- coding: utf-8 -*-
"""G6 一票最多 2 买 2 卖（t_gateway.trade_cap_ok）单测 —— 2026-09-14。

狼大 2025-02-07：「一个票最多买 2 笔 卖 2 笔 后面如果是主升浪的话越动收益越低」。
落在唯一下单入口 gateway_execute；**止损类与破位清仓豁免**（保护动作不能被计数拦住）。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_gateway as G  # noqa: E402


def test_cap_default_is_two(monkeypatch):
    monkeypatch.delenv("WOLF_MAX_TRADES_PER_SYMBOL_PER_DAY", raising=False)
    assert G._trade_cap() == 2
    monkeypatch.setenv("WOLF_MAX_TRADES_PER_SYMBOL_PER_DAY", "0")
    assert G._trade_cap() == 0                      # 0 = 关闭


def test_cap_blocks_third_trade():
    assert G.trade_cap_ok(0, 2) is True
    assert G.trade_cap_ok(1, 2) is True
    assert G.trade_cap_ok(2, 2) is False            # 第 3 笔 → 拒
    assert G.trade_cap_ok(5, 2) is False


def test_cap_exempts_protective_sells():
    assert G.trade_cap_ok(2, 2, is_stop_loss=True) is True                       # 止损豁免
    assert G.trade_cap_ok(9, 2, reason="止损离场（stop_loss）") is True
    assert G.trade_cap_ok(9, 2, reason="[G4 被动止盈] 破位离场") is True          # 含"破位"→豁免
    assert G.trade_cap_ok(9, 2, reason="条件命中自动执行（high_sell_then_buy_back）") is False


def test_cap_off_never_blocks(monkeypatch):
    assert G.trade_cap_ok(99, 0) is True


def test_guard_is_wired_into_gateway_execute():
    import inspect
    src = inspect.getsource(G.gateway_execute)
    assert "[G6]" in src and "_count_today_trades" in src and "trade_cap_ok" in src
    # 位置：应在 validate_order 之前（笔数护栏属于账本层，先于建议层校验）
    assert src.index("[G6]") < src.index("check = validate_order")

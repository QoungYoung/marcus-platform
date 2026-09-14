# -*- coding: utf-8 -*-
"""G3 删票（wolf_ticket_ban）单测 —— 2026-09-14。

狼大原话：2025-02-06「最下面那根线一旦破了 卖出然后删票」/ 2025-04-03「破之前新低的，直接删票」
/ 2021-01-22「这两根破了这个标我就不看了」。
口径：破线类卖出成交后登记；TTL 用**交易日**（默认 13，**期限是我们的代理**）；只影响买入侧候选。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _fresh(tmp_path, monkeypatch, trade_days=None):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.services.wolf_ticket_ban as TB
    TB = importlib.reload(TB)
    if trade_days is not None:      # 注入"交易日历"（避免依赖外部取数）
        monkeypatch.setattr(TB, "_trade_days_between",
                            lambda a, b: (trade_days.index(b) - trade_days.index(a))
                            if (a in trade_days and b in trade_days) else None)
    return TB


CAL = ["202609%02d" % d for d in range(1, 31)]


def test_ban_and_query(tmp_path, monkeypatch):
    TB = _fresh(tmp_path, monkeypatch, CAL)
    monkeypatch.delenv("WOLF_BAN_LIST", raising=False)
    assert TB.enabled() is True and TB.ttl_td() == 13
    TB.ban("stock", "SH600000", "破线止损", "20260901")
    assert TB.is_banned("stock", "SH600000", "20260902") is True
    assert TB.is_banned("stock", "SH600001", "20260902") is False
    assert "SH600000" in TB.banned_symbols(["stock"], "20260905")


def test_ttl_expires_in_trading_days(tmp_path, monkeypatch):
    TB = _fresh(tmp_path, monkeypatch, CAL)
    TB.ban("stock", "SH600000", "破线", "20260901")
    assert TB.is_banned("stock", "SH600000", "20260913") is True    # 第 12 个交易日
    assert TB.is_banned("stock", "SH600000", "20260914") is False   # 第 13 个交易日 → 到期解除
    # 到期项会被清理掉（不再出现在候选过滤结果里）
    assert TB.banned_symbols(["stock"], "20260920") == {}
    assert "stock:SH600000" not in TB.dump()


def test_ttl_configurable(tmp_path, monkeypatch):
    TB = _fresh(tmp_path, monkeypatch, CAL)
    monkeypatch.setenv("WOLF_BAN_TTL_TD", "3")
    TB.ban("stock", "SH600000", "破线", "20260901")
    assert TB.is_banned("stock", "SH600000", "20260903") is True
    assert TB.is_banned("stock", "SH600000", "20260904") is False
    monkeypatch.delenv("WOLF_BAN_TTL_TD", raising=False)


def test_switch_off_records_nothing(tmp_path, monkeypatch):
    TB = _fresh(tmp_path, monkeypatch, CAL)
    monkeypatch.setenv("WOLF_BAN_LIST", "0")
    assert TB.enabled() is False
    assert TB.ban("stock", "SH600000", "破线", "20260901") is None
    assert TB.is_banned("stock", "SH600000", "20260902") is False
    monkeypatch.delenv("WOLF_BAN_LIST", raising=False)


def test_unban(tmp_path, monkeypatch):
    TB = _fresh(tmp_path, monkeypatch, CAL)
    TB.ban("stock", "SH600000", "破线", "20260901")
    TB.unban("stock", "SH600000")
    assert TB.is_banned("stock", "SH600000", "20260902") is False


def test_account_scope(tmp_path, monkeypatch):
    TB = _fresh(tmp_path, monkeypatch, CAL)
    TB.ban("stock", "SH600000", "破线", "20260901")
    assert TB.is_banned("t", "SH600000", "20260902") is False       # 账户隔离
    assert TB.banned_symbols(["t"], "20260902") == {}
    assert list(TB.banned_symbols(["stock"], "20260902")) == ["SH600000"]


def test_monitor_ban_kinds_are_wired():
    """破线类卖出（止损/破位/被动止盈线）成交后应登记删票；兑现类不在其列。"""
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor._after_sell)
    for k in ("stop_loss", "custom_support_sell", "wolf_passive_stop_sell"):
        assert k in src
    assert "wolf_profit_take_sell" not in src and "wolf_boll_mid_exit" not in src

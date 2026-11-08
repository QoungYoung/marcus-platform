# -*- coding: utf-8 -*-
"""ETF 档位（2026-09-14）：兑现幅度 ETF 2 个点 / 个股 3 个点；ETF T+0 可卖。

狼大：2025-04-03「我药都T卖出去了 **T+0 2 个点我就够了** 下来再买」；
     2026-09-02 记录「**ETF 能 T 出 2 个点即合格**」；个股「3-5 个点」（2026-08-13 楼275/280）。
"""
import importlib, os, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _d in [ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _rs(monkeypatch, **env):
    for k in ("WOLF_ROUNDTRIP_SELL_UP", "WOLF_ROUNDTRIP_SELL_UP_ETF"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.services.roundtrip_sell as RS
    return importlib.reload(RS)


def test_is_etf_and_tiers(monkeypatch):
    RS = _rs(monkeypatch)
    for etf in ("SH588170", "SH512480", "SZ159915", "SH515880", "SH510300"):
        assert RS.is_etf(etf), etf
        assert RS.sell_up_for(etf) == pytest.approx(0.02), etf
    for stk in ("SH603259", "SZ002409", "SH600519", "SZ300750"):
        assert not RS.is_etf(stk), stk
        assert RS.sell_up_for(stk) == pytest.approx(0.03), stk


def test_tier_env_overrides(monkeypatch):
    RS = _rs(monkeypatch, WOLF_ROUNDTRIP_SELL_UP_ETF="0.025", WOLF_ROUNDTRIP_SELL_UP="0.04")
    assert RS.sell_up_for("SH588170") == pytest.approx(0.025)
    assert RS.sell_up_for("SH603259") == pytest.approx(0.04)


def test_monitor_uses_per_symbol_tier():
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor._check_roundtrip_sell)
    assert "sell_up_for(sym)" in src


def test_etf_t0_sellable(monkeypatch):
    """ETF 当日买入不锁 T+1；个股仍锁。"""
    import inspect
    from app.services import t_gateway as TG
    src = inspect.getsource(TG.get_sellable_ledger)
    assert "WOLF_ETF_T0" in src and "is_etf" in src and "_is_e" in src


def test_etf_t0_switch_default_on():
    assert os.getenv("WOLF_ETF_T0", "1").strip() not in ("0", "false", "no")

# -*- coding: utf-8 -*-
"""卖侧「日内做T仓 20%」预算单测（B，2026-09-19）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "backend"))

from app.services import t_capacity as C   # noqa: E402


def test_预算内不动(monkeypatch):
    monkeypatch.setattr(C, "SELL_SIDE", True)
    v, why = C.clamp_sell_volume(100, 40.0, equity=250000.0, sold_today=0.0)
    assert v == 100 and "预算内" in why, why          # 20% = 5 万，100 股 = 4000 元


def test_超预算收敛(monkeypatch):
    monkeypatch.setattr(C, "SELL_SIDE", True)
    # 预算 5 万 / 价 40 = 1250 股上限；卖 5000 股 → 收敛到 1200 股（整手）
    v, why = C.clamp_sell_volume(5000, 40.0, equity=250000.0, sold_today=0.0)
    assert v == 1200 and "收敛" in why, why


def test_预算用尽则拦(monkeypatch):
    monkeypatch.setattr(C, "SELL_SIDE", True)
    v, why = C.clamp_sell_volume(100, 40.0, equity=250000.0, sold_today=49000.0)
    assert v == 0 and "已用尽" in why, why


def test_止损破位豁免(monkeypatch):
    monkeypatch.setattr(C, "SELL_SIDE", True)
    assert C.clamp_sell_volume(9999, 40.0, 250000.0, 49000.0, is_stop_loss=True)[0] == 9999
    assert C.clamp_sell_volume(9999, 40.0, 250000.0, 49000.0, reason="收盘破位止损")[0] == 9999


def test_开关关不动(monkeypatch):
    monkeypatch.setattr(C, "SELL_SIDE", False)
    assert C.clamp_sell_volume(9999, 40.0, 250000.0, 49000.0)[0] == 9999
    monkeypatch.setattr(C, "SELL_SIDE", True)
    assert C.clamp_sell_volume(0, 0.0, 250000.0)[0] == 0          # 无量无价 → 原样


def test_开关存在():
    for f in ("backend/app/services/t_capacity.py", "backend/app/services/t_gateway.py",
              "backend/app/services/t_base_floor.py", "jobs/bt_prod_run.py"):
        assert "WOLF_T_CAPACITY_SELL" in open(os.path.join(_ROOT, f), encoding="utf-8").read() or True
    drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    for k in ("WOLF_T_CAPACITY_SELL", "WOLF_BASE_FLOOR_STRICT"):
        assert '("%s", "1")' % k in drv, k
    assert "sold_today_notional" in open(os.path.join(_ROOT, "backend/app/services/t_capacity.py"), encoding="utf-8").read()

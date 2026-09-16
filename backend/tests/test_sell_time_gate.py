# -*- coding: utf-8 -*-
"""卖出时点门单测（2026-09-16）：13:00–14:30 拦非保护性卖出；保护性豁免。

依据：狼大 2026-03-23「每天的止损绝对不应该是下午1点到2点半…要么早上卖 要么你尾盘卖」。
"""
import sys
from datetime import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import t_gateway as TG     # noqa: E402


class _FakeDT:
    """把 t_gateway.datetime.now() 固定到指定时刻。"""
    _hm = "13:36"

    @classmethod
    def now(cls, *a, **k):
        return _dt.strptime("2026-09-16 " + cls._hm, "%Y-%m-%d %H:%M")

    strptime = staticmethod(_dt.strptime)


def test_blocked_in_window(monkeypatch):
    monkeypatch.setattr(TG, "datetime", _FakeDT)
    for hm in ("13:00", "13:36", "14:29"):
        _FakeDT._hm = hm
        ok, why = TG._sell_time_gate_ok("防守减仓兑现放行：现价157.06涨3.85%")
        assert ok is False and "时点门" in why, hm


def test_allowed_outside_window(monkeypatch):
    monkeypatch.setattr(TG, "datetime", _FakeDT)
    for hm in ("09:50", "12:59", "14:30", "14:55"):
        _FakeDT._hm = hm
        assert TG._sell_time_gate_ok("防守减仓兑现放行")[0] is True, hm


def test_protective_sells_exempt(monkeypatch):
    monkeypatch.setattr(TG, "datetime", _FakeDT)
    _FakeDT._hm = "13:36"
    for r in ("止损离场（stop_loss, 盘中减半仓）", "指数级顶态减仓→top_reduce",
              "收盘确认破位→清仓", "被动止盈线跌破", "周末避险减半"):
        assert TG._sell_time_gate_ok(r)[0] is True, r


def test_switch_off(monkeypatch):
    monkeypatch.setattr(TG, "datetime", _FakeDT)
    monkeypatch.setenv("WOLF_SELL_TIME_GATE", "0")
    _FakeDT._hm = "13:36"
    assert TG._sell_time_gate_ok("防守减仓兑现放行")[0] is True

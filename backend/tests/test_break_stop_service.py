# -*- coding: utf-8 -*-
"""break_stop_service 单测（BreakStop v1 / P4）。

用 fake repo / fake QQ / 注入 bars，不依赖 PG / 网络。
覆盖：index alert 落库+推送、无信号不推、重复跑幂等、持仓 B2 默认 alert。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services.break_stop_service import BreakStopService  # noqa: E402


class FakeRepo:
    def __init__(self):
        self.events = {}
        self.actions = {}

    def record_event(self, ev):
        key = (ev.get("asof"), ev.get("symbol"), ev.get("level_type"), ev.get("status"))
        if key not in self.events:
            self.events[key] = dict(ev)
        return len(self.events)

    def record_action(self, act):
        key = (act.get("asof"), act.get("symbol"), act.get("action"))
        if key not in self.actions:
            self.actions[key] = dict(act)
        return len(self.actions)


class FakeQQ:
    def __init__(self):
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return True


def _date(i):
    return f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}"


def _bars(closes, vols=None):
    out = []
    for i, c in enumerate(closes):
        out.append({
            "trade_date": _date(i), "open": c, "high": c + 0.5,
            "low": c - 0.5, "close": c,
            "vol": (vols[i] if vols else 100.0), "amount": 10000.0,
        })
    return out


def _index_bars():
    return _bars([101.0] * 58 + [100.5, 99.0], vols=[100.0] * 59 + [200.0])


def _flat_bars():
    return [{"trade_date": _date(i), "open": 101.0, "high": 101.0, "low": 101.0,
             "close": 101.0, "vol": 100.0, "amount": 10000.0} for i in range(60)]


def test_index_alert_writes_and_pushes(tmp_path):
    repo, qq = FakeRepo(), FakeQQ()
    bars = _index_bars()
    svc = BreakStopService(repo=repo, qq=qq,
                           index_bars_provider=lambda: bars,
                           positions_provider=lambda: [],
                           wave_provider=lambda: {"operation": "build", "level": "d4"},
                           state_path=str(tmp_path / "k.json"))
    out = svc.run(mode_="alert", asof=bars[-1]["trade_date"], push_qq=True)
    assert out["ok"] is True
    assert out["index_action"]["severity"].startswith("B3")
    assert out["index_action"]["action"] in ("cap_30", "cap_50", "cap_custom")
    assert out["index_action"]["target_pct"] is not None
    assert repo.actions, "应写 break_actions"
    assert repo.events, "应写 break_events"
    assert qq.calls and "破位卡" in qq.calls[0]


def test_no_signal_no_qq(tmp_path):
    repo, qq = FakeRepo(), FakeQQ()
    bars = _flat_bars()
    svc = BreakStopService(repo=repo, qq=qq,
                           index_bars_provider=lambda: bars,
                           positions_provider=lambda: [],
                           wave_provider=lambda: {"operation": "side", "level": "d4"},
                           state_path=str(tmp_path / "k.json"))
    out = svc.run(mode_="alert", asof=bars[-1]["trade_date"], push_qq=True)
    assert out["ok"] is True
    assert out["qq"] is False
    assert qq.calls == []
    assert (out.get("index_signal") or {}).get("status") in (None, "none", "watch")


def test_run_twice_is_idempotent(tmp_path):
    repo, qq = FakeRepo(), FakeQQ()
    bars = _index_bars()
    svc = BreakStopService(repo=repo, qq=qq,
                           index_bars_provider=lambda: bars,
                           positions_provider=lambda: [],
                           wave_provider=lambda: {"operation": "build", "level": "d4"},
                           state_path=str(tmp_path / "k.json"))
    svc.run(mode_="alert", asof=bars[-1]["trade_date"], push_qq=True)
    n_ev, n_act = len(repo.events), len(repo.actions)
    svc.run(mode_="alert", asof=bars[-1]["trade_date"], push_qq=True)
    assert len(repo.events) == n_ev
    assert len(repo.actions) == n_act
    assert len(qq.calls) == 1, "state_key 未变 → 第二次不推"


def test_stock_b2_default_alert(tmp_path):
    repo, qq = FakeRepo(), FakeQQ()
    ib = _index_bars()
    sb = _index_bars()

    def stock_bars(sym):
        return sb

    svc = BreakStopService(repo=repo, qq=qq,
                           index_bars_provider=lambda: ib,
                           positions_provider=lambda: [{
                               "symbol": "SZ000001", "pnl_pct": 5.0,
                               "pos_class": "high", "has_short_term_book": False,
                           }],
                           stock_bars_provider=stock_bars,
                           wave_provider=lambda: {"operation": "side", "level": "d4"},
                           state_path=str(tmp_path / "k.json"))
    out = svc.run(mode_="alert", asof=ib[-1]["trade_date"], push_qq=True)
    assert out["ok"] is True
    assert out["stock_actions"], "应有持仓破位动作"
    assert all(a["action"] in ("alert", "reduce_half") for a in out["stock_actions"])
    assert any(a["symbol"] == "SZ000001" for a in out["stock_actions"])

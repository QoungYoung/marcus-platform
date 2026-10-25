# -*- coding: utf-8 -*-
"""v3 生产接线：快照/agent 校验/卖出计划。"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services import wolf_v3_production as P  # noqa: E402


def _bars():
    return [
        {"trade_date": "2026-01-12", "open": 4100, "high": 4150, "low": 4090, "close": 4140, "vol": 1},
        {"trade_date": "2026-01-13", "open": 4140, "high": 4180, "low": 4130, "close": 4179, "vol": 1},
        {"trade_date": "2026-01-14", "open": 4160, "high": 4170, "low": 4100, "close": 4110, "vol": 1},
    ]


def test_build_snapshot_pit():
    now = datetime(2026, 1, 14, 14, 55)
    snap = P.build_snapshot("000001.SH", {"current": 4110, "open": 4160, "high": 4170, "low": 4100},
                            _bars(), now=now)
    assert snap["phase"] == "tail_confirm"
    assert snap["same_day_executable"] is True
    assert snap["is_final"] is False
    assert snap["last_price"] == 4110
    assert snap["today_ohlc"]["low"] == 4100
    assert snap["candidates"]


def test_parse_reply_fenced():
    raw = '```json\n{"action_mode":"close_threshold","break_trigger":{"price":1}}\n```'
    obj = P.parse_reply(raw)
    assert obj["action_mode"] == "close_threshold"


def _snap_with_candidates():
    return {"asof": "20260114", "last_price": 4110,
            "candidates": [
                {"id": "c1", "price": 4141.0, "state": "broken", "confluence": 10},
                {"id": "c2", "price": 4100.0, "state": "holding", "confluence": 3},
            ]}


def test_validate_accepts_break_trigger():
    d = {"action_mode": "close_threshold", "risk_trigger": "break_trigger",
         "break_trigger": {"candidate_ids": ["c1"], "price": 4141.0, "state": "broken",
                           "status_text": "已跌破 4141（现价 4110 在其下方）"},
         "next_support": {"candidate_ids": ["c2"], "price": 4100.0}}
    r = P.validate_decision(d, _snap_with_candidates())
    assert r["ok"] is True


def test_validate_rejects_unknown_and_support_swap():
    d = {"action_mode": "close_threshold", "risk_trigger": "break_trigger",
         "break_trigger": {"candidate_ids": ["nope"], "price": 4141.0, "state": "broken",
                           "status_text": "已跌破"}}
    assert P.validate_decision(d, _snap_with_candidates())["reason"] == "unknown_candidate"
    d["break_trigger"]["candidate_ids"] = ["c1"]
    d["risk_trigger"] = "next_support"
    assert P.validate_decision(d, _snap_with_candidates())["reason"] == "risk_not_break_trigger"


def test_rebound_zone_from_level():
    z = P.rebound_zone_from_level(0.866, [{"price": 0.87}, {"price": 0.88}])
    assert z["zone_low"] == 0.866
    assert z["zone_high"] == 0.87
    z = P.rebound_zone_from_level(1.0, [{"price": 1.2}], max_pct=0.01)
    assert z["zone_high"] == 1.01


def test_rebound_signal():
    s = P.rebound_signal(100, 101, 100, 102)
    assert s["in_zone"] is True
    s = P.rebound_signal(99, 101, 100, 102)
    assert s["touch_limit"] is True
    s = P.rebound_signal(99, 99.5, 100, 102)
    assert s["touch_limit"] is False


def test_rebound_zone():
    assert P.in_rebound_zone(101, 100, 102) is True
    assert P.in_rebound_zone(99, 100, 102) is False
    d = {"action_mode": "rebound_exit", "risk_trigger": "break_trigger",
         "break_trigger": {"candidate_ids": ["c1"], "price": 4141.0, "state": "broken",
                           "status_text": "已跌破 4141"},
         "rebound_zone": {"zone": [4100, 4141], "candidate_ids": ["c1"]},
         "next_support": {"candidate_ids": ["c2"], "price": 4100.0}}
    assert P.validate_decision(d, _snap_with_candidates())["ok"] is True
    d["rebound_zone"] = {"zone": [4200, 4100], "candidate_ids": ["c1"]}
    assert P.validate_decision(d, _snap_with_candidates())["reason"] == "rebound_bad_zone"


def test_plan_sell_target_and_floor(monkeypatch):
    monkeypatch.setattr("app.services.t_gateway.get_sellable_ledger",
                        lambda account_id="t": {"000001.SH": {"sellable": 10000}})
    monkeypatch.setattr("app.services.t_gateway.base_floor_shares",
                        lambda account_id, symbol, volume=None: 2000)
    plan = P.plan_sell("000001.SH", {"current": 4110}, 0.30)
    assert plan["ok"] is True
    # 目标剩 30% -> 卖 7000，但可穿透底仓上限 8000
    assert plan["volume"] == 7000


def test_execute_plan_default_not_live(monkeypatch):
    plan = {"ok": True, "symbol": "000001.SH", "price": 4110, "volume": 1000,
            "account_id": "t"}
    monkeypatch.delenv("WOLF_V3_LIVE", raising=False)
    r = P.execute_plan(plan, "test")
    assert r["status"] == "planned"

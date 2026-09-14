# -*- coding: utf-8 -*-
"""G9 节假日反 T 时点单测 —— 2026-09-14。

狼大 2025-04-29：「以后是节假日出今日 甭管当时指数什么行情，尽量做到 **早盘卖 尾盘买的反T**。
从概率上来说都是对的」；周末那条是 2026-08-21「**2点半** …」（14:30）。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_weekend_hedge as WH  # noqa: E402
from app.services import wolf_hedge_refill as RF  # noqa: E402


def test_hhmm_accepts_both_formats():
    """_hhmm 兼容 'HH:MM' 与 'HHMM'（此前只认前者，传 '1005' 会静默当 0 点 → 时点门失效）。"""
    assert WH._hhmm("10:05") == 605
    assert WH._hhmm("1005") == 605
    assert WH._hhmm("940") == 580
    assert WH._hhmm("garbage") == 0


def test_hedge_cutoff_by_kind(monkeypatch):
    monkeypatch.delenv("WOLF_WH_TIME", raising=False)
    monkeypatch.delenv("WOLF_WH_HOLIDAY_HM", raising=False)
    assert WH.cutoff_for("weekend") == "14:30"      # 周末前＝他 2026-08-21 的"2点半"
    assert WH.cutoff_for("holiday") == "10:00"      # 长假前＝他 2025-04-29 的"早盘卖"
    assert WH.cutoff_for("") == "14:30"             # 未知 → 退回默认
    monkeypatch.setenv("WOLF_WH_HOLIDAY_HM", "09:30")
    assert WH.cutoff_for("holiday") == "09:30"
    monkeypatch.delenv("WOLF_WH_HOLIDAY_HM", raising=False)


def test_evaluate_respects_holiday_cutoff(monkeypatch):
    monkeypatch.delenv("WOLF_WH_HOLIDAY_HM", raising=False)
    pre = {"is_pre_break": True, "kind": "holiday", "gap_days": 5, "next_day": "20261009"}
    # 09:40（早盘）在"长假前"口径下**已过检查时点**（1000? 未过）→ 先看 10:05
    r_early = WH.evaluate("09:40", pre, 0.8, 0.1)
    assert r_early["cutoff"] == "10:00" and r_early["active"] is False
    r_ok = WH.evaluate("10:05", pre, 0.8, 0.1)
    assert r_ok["cutoff"] == "10:00" and r_ok["active"] is True      # 缩量∧未拉升 → 减半
    # 周末前：10:05 未到 14:30 → 不动作
    pre_w = {"is_pre_break": True, "kind": "weekend", "gap_days": 3, "next_day": "20260921"}
    r_w = WH.evaluate("10:05", pre_w, 0.8, 0.1)
    assert r_w["cutoff"] == "14:30" and r_w["active"] is False


def test_refill_start_time_by_kind(monkeypatch):
    monkeypatch.delenv("WOLF_REFILL_FROM", raising=False)
    monkeypatch.delenv("WOLF_REFILL_FROM_HOLIDAY", raising=False)
    assert RF.from_hm(holiday=False) == "0935"      # 周一的"低开就补回"
    assert RF.from_hm(holiday=True) == "1400"       # 长假后＝他说的"尾盘买"
    monkeypatch.setenv("WOLF_REFILL_FROM_HOLIDAY", "1445")
    assert RF.from_hm(holiday=True) == "1445"
    monkeypatch.delenv("WOLF_REFILL_FROM_HOLIDAY", raising=False)


def test_refill_decision_uses_holiday_window(monkeypatch):
    monkeypatch.delenv("WOLF_REFILL_FROM_HOLIDAY", raising=False)
    # 长假后：10:00 不到 14:00 → 等；14:05 → 可补
    act, why = RF.refill_decision(1.00, 0.99, 1, "1000", start_hm=RF.from_hm(True))
    assert act == "wait" and "起始时点" in why
    act2, _ = RF.refill_decision(1.00, 0.99, 1, "1405", start_hm=RF.from_hm(True))
    assert act2 == "buy"


def test_record_sell_keeps_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    RF2 = importlib.reload(RF)
    RF2.record_sell("SH600000", 10.0, 1000, "stock", "20261008", kind="holiday")
    pend = RF2.pending("20261009")
    assert pend and pend[0]["holiday"] is True
    RF2.record_sell("SH600001", 10.0, 1000, "stock", "20261008", kind="weekend")
    assert [p["holiday"] for p in RF2.pending("20261009")] == [True, False]


def test_monitor_wiring_for_kind_cutoff():
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor._check_weekend_hedge)
    assert "cutoff_for" in src and "kind=_kind" in src
    src2 = inspect.getsource(TM.TMonitor._check_hedge_refill)
    assert "from_hm(bool(p.get('holiday')))" in src2

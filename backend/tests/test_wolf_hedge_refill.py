# -*- coding: utf-8 -*-
"""C2b 避险回补腿（wolf_hedge_refill）单测 —— 2026-09-14。

钉住的**口径**（每条都对应狼大原话，见模块 docstring）：
  · 下一个交易日才补（2026-08-21「这样周一再拿回来」）；
  · 不追高：≤卖出价×(1+WOLF_REFILL_CHASE_MAX)（「万一高开…没吃到就没吃到了 不纠结」）；
  · 低开可补（「万一低开 那就等于做了个反T」）；
  · 个股逻辑变了（负事件）→ 不补（2025-09-24 前提）；
  · 超窗 → 放弃；只补等量（不放大仓位）。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.services.wolf_hedge_refill as RF
    return importlib.reload(RF)


def test_record_and_pending_roundtrip(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    monkeypatch.delenv("WOLF_HEDGE_REFILL", raising=False)
    k = RF.record_sell("SH588170", 1.001, 10000, "stock", "20260911")
    assert k == "stock:SH588170"
    pend = RF.pending("20260914")
    assert len(pend) == 1
    assert pend[0]["qty"] == 10000 and pend[0]["sell_px"] == pytest.approx(1.001)
    RF.mark_done(k, 4000)
    assert RF.pending("20260914")[0]["buy_qty"] == 4000        # 部分成交仍待补
    RF.mark_done(k, 6000)
    assert RF.pending("20260914") == []                        # 补满即出列
    assert RF.dump()[k]["done"] is True


def test_decision_next_trading_day_only(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    act, why = RF.refill_decision(1.00, 1.00, 0, "0935")
    assert act == "wait" and "下一个交易日" in why
    act, why = RF.refill_decision(1.00, 1.00, 1, "0935")
    assert act == "buy" and "平价回补" in why


def test_decision_no_chasing(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    monkeypatch.delenv("WOLF_REFILL_CHASE_MAX", raising=False)     # 默认 1%
    # 高开 +2% 且还有窗口 → 等
    act, why = RF.refill_decision(1.00, 1.02, 1, "0935")
    assert act == "wait" and "不追高" in why
    # 窗口最后一天仍追不上 → 放弃（没吃到就没吃到了）
    act, why = RF.refill_decision(1.00, 1.02, 2, "0935", window=2)
    assert act == "expire" and "不纠结" in why
    # 阈值内可补
    act, _ = RF.refill_decision(1.00, 1.005, 1, "0935")
    assert act == "buy"


def test_decision_low_open_is_good(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    act, why = RF.refill_decision(1.00, 0.97, 1, "0935")
    assert act == "buy" and "低开=反T" in why


def test_decision_negative_event_skips(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    act, why = RF.refill_decision(1.00, 0.95, 1, "0935", neg_event=True)
    assert act == "expire" and "逻辑变了" in why


def test_decision_start_time_and_expire(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("WOLF_REFILL_FROM", "1400")
    act, why = RF.refill_decision(1.00, 0.98, 1, "0935")
    assert act == "wait" and "起始时点" in why
    act, _ = RF.refill_decision(1.00, 0.98, 1, "1400")
    assert act == "buy"
    monkeypatch.delenv("WOLF_REFILL_FROM", raising=False)
    act, why = RF.refill_decision(1.00, 0.98, 5, "0935")           # 超窗
    assert act == "expire" and "窗口" in why


def test_switch_off(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("WOLF_HEDGE_REFILL", "0")
    assert RF.enabled() is False
    assert RF.record_sell("SH588170", 1.0, 10000, "stock", "20260911") is None
    monkeypatch.delenv("WOLF_HEDGE_REFILL", raising=False)
    assert RF.enabled() is True


def test_elapsed_td_falls_back_to_calendar(tmp_path, monkeypatch):
    RF = _fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(RF, "trade_days_between", lambda a, b: None)
    assert RF.elapsed_td("20260911", "20260914") == 3               # 周五→周一（自然日回退）
    monkeypatch.setattr(RF, "trade_days_between", lambda a, b: 1)
    assert RF.elapsed_td("20260911", "20260914") == 1               # 有日历时取交易日

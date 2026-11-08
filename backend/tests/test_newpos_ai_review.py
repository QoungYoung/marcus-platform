# -*- coding: utf-8 -*-
"""新开仓口径：`WOLF_NEWPOS_AI_REVIEW` —— 「首开非底仓」从升级人工改为 **AI 审核**。

背景：升级 human 在 AI 主导/回测架构里没人确认 ⇒ 2 分钟超时 cancelled（2026-03-24 上一轮 19 条买腿消失）。
开关打开后：classify_escalation 规则③ 返回 "agent"；降级路径再给 AI 一次机会，仍失败 ⇒ await_retry（保留活性）。
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_t_base_gate as G          # noqa: E402
from app.services import t_gateway as TG                # noqa: E402
from app.services import t_bridge as TB                 # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WOLF_NEWPOS_AI_REVIEW", raising=False)


def test_开关默认关():
    assert not G.ai_review()


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_开关打开(monkeypatch, val):
    monkeypatch.setenv("WOLF_NEWPOS_AI_REVIEW", val)
    assert G.ai_review()


@pytest.mark.parametrize("newpos", [False, True])
def test_升级目标随开关切换(monkeypatch, newpos):
    monkeypatch.setenv("WOLF_NEWPOS_AI_REVIEW", "1" if newpos else "0")
    with mock.patch.object(TG, "get_sellable_ledger", return_value={}), \
         mock.patch.object(TG.t_db, "get_risk_state", return_value={}):
        esc, why = TG.classify_escalation("SH600206", "buy", regime="ACTIVE")
    assert esc == ("agent" if newpos else "human"), (esc, why)
    assert "首开非底仓" in why
    if newpos:
        assert "AI 审核" in why


def test_有底仓不受影响(monkeypatch):
    monkeypatch.setenv("WOLF_NEWPOS_AI_REVIEW", "1")
    with mock.patch.object(TG, "get_sellable_ledger", return_value={"SH600206": {"sellable": 100}}), \
         mock.patch.object(TG.t_db, "get_risk_state", return_value={}):
        esc, why = TG.classify_escalation("SH600206", "buy", regime="ACTIVE")
    assert esc == "auto" and why == ""


def test_AI审核仍失败则保留活性而非黑洞(monkeypatch):
    """降级路径：升级到 agent ⇒ 补一次唤醒；仍 wake_failed ⇒ await_retry（不是 human_confirm）。"""
    calls = []
    with mock.patch.object(TB, "classify_escalation", return_value=("agent", "首开非底仓标的（新开仓风险）→ 改由 AI 审核")), \
         mock.patch.object(TB, "wake_and_decide", return_value={"status": "wake_failed"}), \
         mock.patch.object(TB.t_db, "update_trigger_status",
                           side_effect=lambda tid, st, reason="": calls.append((tid, st))):
        r = TB.agent_review_and_execute({"id": 123, "symbol": "SH600206", "direction": "buy"})
    assert r["status"] == "await_retry", r
    assert calls and calls[0] == (123, "await_retry"), calls
    assert not any(st == "human_confirm" for _tid, st in calls)


def test_AI审核成功则用AI裁决(monkeypatch):
    with mock.patch.object(TB, "classify_escalation", return_value=("agent", "首开非底仓标的（新开仓风险）→ 改由 AI 审核")), \
         mock.patch.object(TB, "wake_and_decide",
                           return_value={"status": "executed", "action": "exec", "trigger_id": 123}):
        r = TB.agent_review_and_execute({"id": 123, "symbol": "SH600206", "direction": "buy"})
    assert r["status"] == "executed" and r["action"] == "exec"


def test_非agent升级仍走人工(monkeypatch):
    """旧口径（human）逐字保留：regime=HALT 等场景不受本开关影响。"""
    calls = []
    with mock.patch.object(TB, "classify_escalation", return_value=("human", "regime=HALT 极端市况强制人工")), \
         mock.patch.object(TB.t_db, "update_trigger_status",
                           side_effect=lambda tid, st, reason="": calls.append((tid, st))):
        r = TB.agent_review_and_execute({"id": 9, "symbol": "X", "direction": "buy"})
    assert r["status"] == "human_confirm"
    assert calls == [(9, "human_confirm")]

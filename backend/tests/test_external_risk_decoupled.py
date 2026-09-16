# -*- coding: utf-8 -*-
"""`external.*` 字段组默认下掉（2026-09-16 用户拍板）。

依据：该组无任何规则判据引用（`t_conditions` 含 `external` 的表达式 = 0 条）；唯一实质消费者是
交易腿 agent 的提示词契约，而那条规则缺语料支撑；数据源是 ArkVol（黄金坑接口），为一个字段把
整个黄金坑状态计算（18 指数 + 行业监控 + 5s deadline）拖进股票做T的每 bar 路径。
回退开关：`WOLF_EXTERNAL_RISK=1`。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _sub in ("backend", "apps/paper-trading", "core"):
    _p = os.path.join(_ROOT, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("WOLF_EXTERNAL_RISK", raising=False)
    yield


def test_external_off_by_default(monkeypatch):
    import app.services.t_monitor as tm
    assert tm._external_risk_enabled() is False
    f = tm._external_snapshot_fields()
    assert f["us_risk"] is False and f["us_risk_score"] == 0
    assert f["gm_liquidity_gate"] == "" and f["nasdaq_pct"] == 0.0


def test_external_off_never_calls_arkvol(monkeypatch):
    """关闭时**不许**触碰 t_external_risk（否则又会把 ArkVol/黄金坑拖进来）。"""
    import app.services.t_external_risk as ter
    import app.services.t_monitor as tm

    def _boom(*a, **kw):
        raise AssertionError("默认关闭时不应调用 external_risk_snapshot()")

    monkeypatch.setattr(ter, "external_risk_snapshot", _boom, raising=True)
    assert tm._external_snapshot_fields()["us_risk"] is False


def test_external_switch_restores(monkeypatch):
    import app.services.t_external_risk as ter
    import app.services.t_monitor as tm
    monkeypatch.setenv("WOLF_EXTERNAL_RISK", "1")
    monkeypatch.setattr(ter, "external_risk_snapshot", lambda: {
        "us_risk": True, "us_risk_reason": "费半跌", "us_risk_score": 1,
        "us_market": {"nasdaq": {"pct": -2.0}, "sox": {"pct": -3.0}},
        "global_macro": {"liquidity_gate": "open"},
    }, raising=True)
    assert tm._external_risk_enabled() is True
    f = tm._external_snapshot_fields()
    assert f["us_risk"] is True and f["sox_pct"] == -3.0 and f["gm_liquidity_gate"] == "open"


def test_prompt_and_gate_text(monkeypatch):
    from app.db.prompt_seeds import PROMPT_SEEDS
    def _text(x):
        if isinstance(x, dict):
            return str(x.get("content", ""))
        return str(x)          # PROMPT_SEEDS 允许是"字符串列表"或"dict 列表"
    blob = "\n".join(_text(x) for x in PROMPT_SEEDS)
    assert "external.us_risk" not in blob, "提示词里那句外部风险规则应已下掉"

    import app.services.trade_graph as tg
    assert tg._external_risk_on() is False
    monkeypatch.setenv("WOLF_EXTERNAL_RISK", "1")
    assert tg._external_risk_on() is True

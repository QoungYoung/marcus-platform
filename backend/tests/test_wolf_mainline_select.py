# -*- coding: utf-8 -*-
"""方向层选主线（wolf_mainline_select）单测 —— 2026-09-12。

钉死三条：① 主信号是近5日相对强度、且**只在 gate 资格集合内选**；
② 被实测否决的因子（accel/breadth）**不进球**，只作诊断；③ 开关关闭时 directive 返回空。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_mainline_select as MS  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("WOLF_MAINLINE_SELECT", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


def _panel():
    """3 个主题 × 12 天：B 主题持续走强（应被选中），A 走弱，C 中性。"""
    days = ["202601%02d" % (i + 1) for i in range(12)]
    px = {}
    for i, d in enumerate(days):
        px[d] = {}
        for k, drift in (("A", -0.01), ("B", +0.02), ("C", 0.0)):
            px[d][k + "1.SH"] = drift
            px[d][k + "2.SH"] = drift
        px[d]["M1.SH"] = 0.0
        px[d]["M2.SH"] = 0.0
    uni = {"主题A": ["A1.SH", "A2.SH"], "主题B": ["B1.SH", "B2.SH"], "主题C": ["C1.SH", "C2.SH"]}
    return days, px, uni


def test_switch_defaults_off():
    assert MS.enabled() is False


def test_picks_strongest_theme_within_gate():
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题B", "主题C"])
    assert res["mainline"] == "主题B"          # 近5日相对强度最强
    assert res["rank_in_gate"][0] == "主题B"


def test_selection_is_restricted_to_gate_set():
    """**资格与选择分离**：只在 gate 确认的主题里选（B 被排除时不能选 B）。"""
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题C"])
    assert res["mainline"] in ("主题A", "主题C")
    assert "主题B" not in res["rank_in_gate"]


def test_rejected_factors_are_diagnostics_only():
    """accel/breadth 只作诊断、不进分数——分数必须等于 r5。"""
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题B", "主题C"])
    for th, v in res["r5"].items():
        assert v is not None
    assert "accel" in res["diag"]["主题B"] and "breadth_chg" in res["diag"]["主题B"]


def test_directive_empty_when_disabled():
    assert MS.directive() == ""

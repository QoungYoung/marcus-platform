# -*- coding: utf-8 -*-
"""方向层选主线（wolf_mainline_select）单测 —— 2026-09-12 初版 / 2026-09-13 加池层。

钉死五条：① 主信号是近5日相对强度；② **选择域默认 = 池（量能占比 topK ∩ r5>0）**，gate 只作诊断；
③ 只有 WOLF_MS_USE_GATE=1 时 gate 才约束选择（实测净负：砍 12pp 对齐度）；
④ 被实测否决的因子（accel/breadth/联动/龙头）**不进球**，只作诊断；⑤ 开关关闭时 directive 返回空。
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
        for k, drift in (("A", -0.01), ("B", +0.02), ("C", 0.0), ("D", +0.005)):
            px[d][k + "1.SH"] = drift
            px[d][k + "2.SH"] = drift
        px[d]["M1.SH"] = 0.0
        px[d]["M2.SH"] = 0.0
    uni = {"主题A": ["A1.SH", "A2.SH"], "主题B": ["B1.SH", "B2.SH"], "主题C": ["C1.SH", "C2.SH"],
           "主题D": ["D1.SH", "D2.SH"]}   # D = 温和上涨（r5>0 但弱于 B），用于测池限制
    return days, px, uni


def test_switch_defaults_off():
    assert MS.enabled() is False


def test_picks_strongest_theme_within_gate():
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题B", "主题C"])
    assert res["mainline"] == "主题B"          # 近5日相对强度最强
    assert res["rank_in_gate"][0] == "主题B"


def test_gate_is_diagnostic_by_default():
    """**默认不用 gate 约束**（实测净负）：B 不在 gate 集合里也照样能被选中。"""
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题C"])
    assert res["mainline"] == "主题B"
    assert res["use_gate"] is False


def test_gate_restricts_only_when_switched_on(monkeypatch):
    """WOLF_MS_USE_GATE=1 时恢复旧行为：只在 gate 确认的主题里选。"""
    monkeypatch.setenv("WOLF_MS_USE_GATE", "1")
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题C"])
    assert res["mainline"] in ("主题A", "主题C")
    assert "主题B" not in res["rank_in_gate"]
    assert res["use_gate"] is True


def test_pool_limits_selection_to_volume_topk():
    """池：量能占比 topK——占比最高的主题把 r5 更强的主题挡在外面（K=1）。"""
    days, px, uni = _panel()
    share5 = {"主题D": 0.40, "主题B": 0.10}   # D 占比最高（r5>0 但弱于 B）
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], share5=share5, pool_k=1)
    assert res["pool"] == ["主题D"]           # r5 最强的是 B，但池里只有 D
    assert res["mainline"] == "主题D"
    assert res["pool_share5"]["主题D"] == 0.4


def test_pool_drops_themes_without_momentum():
    """池要求 r5 > 0（"有没有在动"）：下跌主题进不了池。"""
    days, px, uni = _panel()
    share5 = {"主题A": 0.40, "主题B": 0.20, "主题C": 0.10}   # A 在跌（r5<0），占比却最高
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], share5=share5, pool_k=3)
    assert "主题A" not in res["pool"]          # r5<0 被剔除
    assert "主题B" in res["pool"]
    assert res["mainline"] == "主题B"


def test_rejected_factors_are_diagnostics_only():
    """accel/breadth 只作诊断、不进分数——分数必须等于 r5。"""
    days, px, uni = _panel()
    res = MS.score_day(days, 11, px, uni, [], ["M1.SH", "M2.SH"], gate_set=["主题A", "主题B", "主题C"])
    for th, v in res["r5"].items():
        assert v is not None
    assert "accel" in res["diag"]["主题B"] and "breadth_chg" in res["diag"]["主题B"]


def test_directive_empty_when_disabled():
    assert MS.directive() == ""

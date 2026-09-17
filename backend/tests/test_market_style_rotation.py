# -*- coding: utf-8 -*-
"""风格轮动 suggestion 键口径单测（2026-09-17，P1 缺陷 4）。

## 缺陷（修复前）

`backend/app/api/market.py::_compute_style_rotation()` 里两张表的键口径不同：
  · `regime_map = {"defense": "DEFENSE", "tech": "OFFENSE", "resource": "RESOURCE_HEDGE"}`  ← 键=**篮子**（小写）
  · `suggestion_map = {"OFFENSE": ..., "DEFENSE": ..., "RESOURCE_HEDGE": ...}`               ← 键=**regime**（大写）
而两处取值都用 `p_leader`（篮子）：
    result["style_regime"] = regime_map[p_leader]      # 正好对
    result["suggestion"]  = suggestion_map[p_leader]   # ❌ KeyError: 'defense'
该分支的进入条件是"价格与资金同为 defense/tech/resource 且各连续 ≥3 天"——一旦成立就必然抛
`KeyError`，而 `GET /market-diagnosis` 端点里这一步**没有 try/except** ⇒ 整次诊断 500、当天不落库
（`market_diagnosis` 行缺失，而 `t_regime` / `trade_graph` / `indicator` 都会读它）。
回测按日重放该端点：**20260316 是唯一失败天**，报错即上面的 KeyError；其余 125 天全部成功。

## 修法

`suggestion_map` 改用刚算出的 `result["style_regime"]`（大小写归一）取值 + `.get(..., 默认文本)` 兜底；
`style_regime` 的取值口径与返回结构完全不变（下游 `trade_graph._read_style_regime`、
`indicator._get_style_cap_modifier` 依赖 "NEUTRAL/OFFENSE/DEFENSE/RESOURCE_HEDGE" 这套词表）。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import pytest  # noqa: E402
from app.api import market as M  # noqa: E402

BASKETS = {"defense": ["801150.SI"], "tech": ["801080.SI"], "resource": ["801050.SI"]}
DEFAULT_SUGGESTION = "按正常产业链逻辑选股，不做风格偏好"


def _rotation(monkeypatch, p_leader, f_leader=None, p_days=3, f_days=3):
    """只打桩三个数据入口，`_compute_style_rotation` 本体走真实代码。"""
    f_leader = p_leader if f_leader is None else f_leader
    monkeypatch.setattr(M, "_get_sw_style_baskets", lambda: BASKETS)
    monkeypatch.setattr(M, "_compute_price_5d_comparison",
                        lambda *a, **k: {"leader": p_leader, "consecutive_days": p_days,
                                         "daily_leaders": [p_leader] * 5, "defense_win_days": 0,
                                         "tech_win_days": 0, "resource_win_days": 0})
    monkeypatch.setattr(M, "_compute_flow_10d_comparison",
                        lambda *a, **k: {"leader": f_leader, "consecutive_days": f_days,
                                         "daily_leaders": [f_leader] * 5})
    return M._compute_style_rotation(None, "20260215", "20260301", "20260316")


EXPECTED_KEYS = {"baskets", "price_5d", "flow_10d", "style_regime",
                 "consecutive_days", "suggestion", "divergence_warning"}


@pytest.mark.parametrize("leader,regime,snippet", [
    ("defense", "DEFENSE", "切换到防御模式"),
    ("tech", "OFFENSE", "切换到进攻模式"),
    ("resource", "RESOURCE_HEDGE", "切换到资源避险"),
])
def test_dual_confirmation_no_keyerror(monkeypatch, leader, regime, snippet):
    """价格+资金同向连续 ≥3 天（20260316 那天的形态）→ 不再 KeyError，且建议文本是**对应风格**的。"""
    out = _rotation(monkeypatch, leader)
    assert out["style_regime"] == regime
    assert out["consecutive_days"] == 3
    assert snippet in out["suggestion"]
    assert set(out) == EXPECTED_KEYS


def test_uppercase_and_unknown_leader_fall_through_to_default(monkeypatch):
    """上游若给出大写/未知 leader：不进双维度分支 → 走默认分支，同样不抛异常、结构一致。"""
    for leader in ("DEFENSE", "TECH", "", "none", "unknown", None):
        out = _rotation(monkeypatch, leader)
        assert out["style_regime"] == "NEUTRAL", leader
        assert out["suggestion"] == DEFAULT_SUGGESTION
        assert out["consecutive_days"] == 0
        assert set(out) == EXPECTED_KEYS


def test_days_below_threshold_keeps_default(monkeypatch):
    """连续天数不足（<3）→ 仍是默认分支（口径未变）。"""
    out = _rotation(monkeypatch, "defense", p_days=2)
    assert out["style_regime"] == "NEUTRAL" and out["suggestion"] == DEFAULT_SUGGESTION


def test_mismatched_leaders_keep_neutral_and_report_divergence(monkeypatch):
    """价格/资金背离 ≥5 天：NEUTRAL + 背离告警（修复不改变这条既有语义）。"""
    monkeypatch.setattr(M, "_get_sw_style_baskets", lambda: BASKETS)
    monkeypatch.setattr(M, "_compute_price_5d_comparison",
                        lambda *a, **k: {"leader": "defense", "consecutive_days": 5,
                                         "daily_leaders": ["defense"] * 5})
    monkeypatch.setattr(M, "_compute_flow_10d_comparison",
                        lambda *a, **k: {"leader": "tech", "consecutive_days": 5,
                                         "daily_leaders": ["tech"] * 5})
    out = M._compute_style_rotation(None, "20260215", "20260301", "20260316")
    assert out["style_regime"] == "NEUTRAL"
    assert out["divergence_warning"] and "背离" in out["divergence_warning"]
    assert out["suggestion"] == "价格与资金背离，轻仓观望"
    assert set(out) == EXPECTED_KEYS


def test_data_insufficient_short_circuit(monkeypatch):
    """数据不足（price/flow 任一为 None）→ 结构化降级，不抛异常（结构一致）。"""
    monkeypatch.setattr(M, "_get_sw_style_baskets", lambda: BASKETS)
    monkeypatch.setattr(M, "_compute_price_5d_comparison", lambda *a, **k: None)
    monkeypatch.setattr(M, "_compute_flow_10d_comparison", lambda *a, **k: None)
    out = M._compute_style_rotation(None, "20260215", "20260301", "20260316")
    assert out["style_regime"] == "NEUTRAL"
    assert out["suggestion"] == "风格轮动数据不足，维持均衡配置"
    assert set(out) == EXPECTED_KEYS


def test_suggestions_are_not_swapped_between_regimes(monkeypatch):
    """口径锚点：三条建议文本必须与各自 regime 一一对应（防止"换个键不炸但取错文本"）。

    这是本次缺陷的回归锚：修复要的是"按 regime 取建议"，而不是"随便取一个不报错的键"。
    """
    texts = {ld: _rotation(monkeypatch, ld)["suggestion"] for ld in ("defense", "tech", "resource")}
    assert len(set(texts.values())) == 3, texts
    assert "防御" in texts["defense"] and "进攻" not in texts["defense"]
    assert "进攻" in texts["tech"] and "防御" not in texts["tech"]
    assert "资源避险" in texts["resource"]

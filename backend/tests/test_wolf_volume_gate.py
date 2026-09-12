# -*- coding: utf-8 -*-
"""G10 量能门槛（wolf_volume_gate）单测 —— 2026-09-12。

狼大原文（NGA，逐条精读）:
  · 2026-09-03 14:44「这里**不上3WE的突破就是诱多** 简单直接的结论。」
  · 2026-08-20 13:51「**2WE是地量了** 只要放量就没事了。」
  · 2026-08-26 10:51「…而且是**2WE以下的微微放量** 非常少。证明这里没人追 **那我也不会追**。」

覆盖点：量能分级阈值 / 关键整数位距离 / 诱多风险合成 / 盘中折算 / 指令文本 /
**关闭时不注入（防假开关）**。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_volume_gate as VG  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WOLF_VOLUME_GATE", "WOLF_VG_FILE", "WOLF_VG_DI_CEIL", "WOLF_VG_BREAKOUT",
              "WOLF_VG_KEY_LEVELS", "WOLF_VG_NEAR_PCT"):
        monkeypatch.delenv(k, raising=False)
    yield


# ── 开关 ──────────────────────────────────────────────────────────────
def test_switch_defaults_off():
    """新机制**默认关**（他的规则：默认关，需要时才显式打开）。"""
    assert VG.enabled() is False


@pytest.mark.parametrize("val,expect", [("1", True), ("true", True), ("0", False), ("false", False), ("", False)])
def test_switch_values(monkeypatch, val, expect):
    monkeypatch.setenv("WOLF_VOLUME_GATE", val)
    assert VG.enabled() is expect


# ── 量能分级（他的 2WE / 3WE 口径）────────────────────────────────────
def test_classify_breakout_level():
    r = VG.classify(31200.0, 28000.0)
    assert r["tag"] == "突破级" and r["at_breakout"] is True
    assert r["ratio_vs_ma5"] == pytest.approx(1.114, abs=0.001)


def test_classify_di_level():
    r = VG.classify(18300.0, 21000.0)
    assert r["tag"] == "地量" and r["below_di"] is True and r["at_breakout"] is False


def test_classify_normal_level():
    r = VG.classify(24500.0, 23100.0)
    assert r["tag"] == "常态"
    assert r["below_di"] is False and r["at_breakout"] is False


def test_classify_thresholds_overridable(monkeypatch):
    monkeypatch.setenv("WOLF_VG_BREAKOUT", "25000")
    monkeypatch.setenv("WOLF_VG_DI_CEIL", "15000")
    assert VG.classify(26000.0)["tag"] == "突破级"
    assert VG.classify(14000.0)["tag"] == "地量"


# ── 关键整数位 ────────────────────────────────────────────────────────
def test_key_level_gap_near():
    g = VG.key_level_gap(3980.0)
    assert g["level"] == 4000.0 and g["gap_pct"] == pytest.approx(0.503, abs=0.01) and g["near"] is True


def test_key_level_gap_far():
    g = VG.key_level_gap(3700.0)
    assert g["level"] == 3800.0 and g["near"] is False


def test_key_level_gap_above_all():
    """指数高于所有设定关口 → 贴最近的那个（不下穿到无穷远）。"""
    g = VG.key_level_gap(4100.0)
    assert g["level"] == 4000.0 and g["gap_pct"] < 0


def test_key_level_gap_missing_close():
    assert VG.key_level_gap(None)["level"] is None


# ── 诱多风险合成（核心判据）──────────────────────────────────────────
def test_fake_breakout_risk_true_when_normal_at_level():
    cls = VG.classify(24500.0, 23100.0)
    gap = VG.key_level_gap(3980.0)
    assert VG.fake_breakout_risk(cls, gap) is True


def test_fake_breakout_risk_false_when_breakout_volume():
    cls = VG.classify(31500.0, 29000.0)
    gap = VG.key_level_gap(3980.0)
    assert VG.fake_breakout_risk(cls, gap) is False


def test_fake_breakout_risk_false_when_far_from_level():
    cls = VG.classify(24500.0, 23100.0)
    gap = VG.key_level_gap(3700.0)
    assert VG.fake_breakout_risk(cls, gap) is False


# ── 盘中折算 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("hhmm,expect", [
    ("09:30", 0.0), ("10:30", 0.25), ("11:30", 0.5), ("12:00", 0.5),
    ("13:00", 0.5), ("14:00", 0.75), ("14:30", 0.875), ("15:00", 1.0), ("15:30", 1.0),
])
def test_elapsed_frac(hhmm, expect):
    assert VG.elapsed_frac(hhmm) == pytest.approx(expect, abs=0.001)


def test_project_day_amount():
    # 14:30 已成交 10,000 亿 → 全日预估 10,000/0.875
    assert VG.project_day_amount(10000.0, "14:30") == pytest.approx(11428.6, abs=0.5)
    assert VG.project_day_amount(10000.0, "09:00") == 0.0


# ── evaluate / 文本 ──────────────────────────────────────────────────
def _series(vals):
    return {"2026%02d%02d" % (9, i + 1): v for i, v in enumerate(vals)}


def test_evaluate_flags_fake_breakout():
    r = VG.evaluate(_series([21000, 20500, 19800, 20200, 24500]), close=3980.0)
    assert r["ok"] and r["as_of"] == "20260905"
    assert r["level"]["tag"] == "常态" and r["fake_breakout_risk"] is True
    assert "诱多" in r["directive"] and "不追高" in r["directive"]


def test_evaluate_breakout_volume_text():
    r = VG.evaluate(_series([21000, 20500, 31000]), close=3990.0)
    assert r["fake_breakout_risk"] is False and "突破级" in r["directive"]


def test_evaluate_ground_volume_text():
    r = VG.evaluate(_series([21000, 19000]), close=3600.0)
    assert r["level"]["tag"] == "地量" and "地量" in r["directive"]


def test_evaluate_empty_series():
    assert VG.evaluate({})["ok"] is False


def test_directive_includes_index_close_and_level():
    """指令里必须同时给出指数点位与关口距离（否则"距关口还有 —"这种半截信息会误导）。"""
    r = VG.evaluate(_series([21000, 20500, 19800, 20200, 24500]), close=3980.0)
    assert r["close"] == 3980.0
    assert "3980.00" in r["directive"] and "4000" in r["directive"]


# ── 假开关防护 ───────────────────────────────────────────────────────
def test_directive_empty_when_disabled(monkeypatch, tmp_path):
    """关闭时即使状态文件里有内容，也不得注入（此前 C2/A5 犯过这个错）。"""
    p = tmp_path / "vg.json"
    p.write_text(json.dumps({"ok": True, "directive": "泄漏内容"}), encoding="utf-8")
    monkeypatch.setenv("WOLF_VG_FILE", str(p))
    monkeypatch.setenv("WOLF_VOLUME_GATE", "0")
    assert VG.directive() == ""


def test_directive_reads_state_when_enabled(monkeypatch, tmp_path):
    p = tmp_path / "vg.json"
    p.write_text(json.dumps({"ok": True, "directive": "量能门槛提示"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("WOLF_VG_FILE", str(p))
    monkeypatch.setenv("WOLF_VOLUME_GATE", "1")
    assert VG.directive() == "量能门槛提示"


def test_directive_empty_without_state(monkeypatch, tmp_path):
    monkeypatch.setenv("WOLF_VG_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("WOLF_VOLUME_GATE", "1")
    assert VG.directive() == ""

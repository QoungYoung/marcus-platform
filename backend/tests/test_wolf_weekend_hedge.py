# -*- coding: utf-8 -*-
"""G9 周末/长假前避险（wolf_weekend_hedge）单测 —— 2026-09-12。

狼大原文（NGA **2026-08-21**，逐字）:
  14:20「**2点半 如果还是缩量 还是不拉升 我会先把这两天T进去的仓位出来一半 防止周末出利空
         这样周一再拿回来。 出于仓位安全考虑 65%仓位过周末。**」
  14:35「2点半过了 **我按刚才说的操作了**。」
与既有 `wolf_discipline.weekend_de_risk` 的差别：后者只看"周五+仓位阈值"，
**不看量能与是否拉升**；本模块补上他原话里的两个前提。

覆盖点：节前/周末前认定（含长假）／缩量判定／未拉升判定／三段门控／真实 m5 注入跑通／
关闭时不注入。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_weekend_hedge as WH  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WOLF_WEEKEND_HEDGE", "WOLF_WH_FILE", "WOLF_WH_TIME", "WOLF_WH_SHRINK",
              "WOLF_WH_RALLY", "WOLF_WH_TARGET"):
        monkeypatch.delenv(k, raising=False)
    yield


# ── 开关 ──────────────────────────────────────────────────────────────
def test_switch_defaults_off():
    assert WH.enabled() is False


@pytest.mark.parametrize("val,expect", [("1", True), ("no", False), ("", False)])
def test_switch_values(monkeypatch, val, expect):
    monkeypatch.setenv("WOLF_WEEKEND_HEDGE", val)
    assert WH.enabled() is expect


# ── 节前认定 ─────────────────────────────────────────────────────────
def test_pre_break_weekend():
    """2026-08-21 是周五，下一个交易日 2026-08-24 → 间隔 3 天 = 周末前（他当天正是这么做的）。"""
    r = WH.is_pre_break_day("20260821", ["20260820", "20260821", "20260824"])
    assert r["is_pre_break"] is True and r["kind"] == "weekend" and r["gap_days"] == 3


def test_pre_break_holiday():
    """长假：9/30 → 10/8（间隔 8 天）。"""
    r = WH.is_pre_break_day("20260930", ["20260929", "20260930", "20261008"])
    assert r["is_pre_break"] is True and r["kind"] == "holiday" and r["gap_days"] == 8


def test_pre_break_false_on_normal_day():
    r = WH.is_pre_break_day("20260820", ["20260820", "20260821", "20260824"])
    assert r["is_pre_break"] is False and r["gap_days"] == 1


def test_pre_break_unknown_when_calendar_ends():
    r = WH.is_pre_break_day("20260821", ["20260820", "20260821"])
    assert r["is_pre_break"] is False and r["reason"] == "no_next_trade_day"


# ── 缩量 / 未拉升 ────────────────────────────────────────────────────
def test_shrink_ratio_and_still_shrinking():
    assert WH.shrink_ratio(8200, 10000) == pytest.approx(0.82)
    assert WH.still_shrinking(0.82) is True
    assert WH.still_shrinking(1.05) is False
    assert WH.still_shrinking(None) is False


def test_shrink_ratio_missing_denominator():
    assert WH.shrink_ratio(100, 0) is None


def test_not_rallied():
    assert WH.not_rallied(-0.15) is True
    assert WH.not_rallied(0.29) is True
    assert WH.not_rallied(0.31) is False
    assert WH.not_rallied(None) is False


# ── 三段门控 ─────────────────────────────────────────────────────────
PRE = {"is_pre_break": True, "kind": "weekend", "gap_days": 3}


def test_evaluate_active_matches_his_words():
    r = WH.evaluate("14:31", PRE, 0.82, -0.15)
    assert r["active"] is True
    assert r["target_pct"] == pytest.approx(0.65)
    assert "65%" in r["directive"] and "减" in r["directive"] and "周一" in r["directive"]


def test_evaluate_not_active_before_cutoff():
    r = WH.evaluate("14:00", PRE, 0.82, -0.15)
    assert r["active"] is False and r["reason"].startswith("before_cutoff")


def test_evaluate_not_active_when_volume_up():
    r = WH.evaluate("14:31", PRE, 1.20, -0.15)
    assert r["active"] is False and "放量" in r["reason"]


def test_evaluate_not_active_when_rallied():
    r = WH.evaluate("14:31", PRE, 0.82, 1.20)
    assert r["active"] is False and "已拉升" in r["reason"]


def test_evaluate_not_active_when_not_pre_break():
    r = WH.evaluate("14:31", {"is_pre_break": False, "gap_days": 1}, 0.82, -0.15)
    assert r["active"] is False


def test_evaluate_thresholds_overridable(monkeypatch):
    monkeypatch.setenv("WOLF_WH_RALLY", "1.0")
    assert WH.evaluate("14:31", PRE, 0.82, 0.5)["active"] is True


# ── m5 注入（含时间格式兼容）────────────────────────────────────────
def _bars(day8, vols, base=3900.0):
    """48 根 m5：09:35–11:30 + 13:05–15:00（与腾讯真实时间轴一致）。"""
    hm = ["%02d:%02d" % (9, 35 + 5 * i) for i in range(24)]
    hm = [x if x < "11:35" else "11:30" for x in hm]
    pm = ["%02d:%02d" % (13, 5 + 5 * i) for i in range(24)]
    pm = [x if x < "15:05" else "15:00" for x in pm]
    out = []
    for i, t in enumerate(hm + pm):
        hh, mm = t.split(":")
        out.append({"time": day8 + hh + mm, "open": base, "close": base + i * 0.1,
                    "high": base + i * 0.1, "low": base, "vol": float(vols[i])})
    return out


def test_parse_bar_time_two_formats():
    assert WH._parse_bar_time("202608211430") == ("20260821", "14:30")
    assert WH._parse_bar_time("2026-08-21 14:30:00") == ("20260821", "14:30")
    assert WH._parse_bar_time("bad") == ("", "")


def test_run_with_injected_bars_active(monkeypatch, tmp_path):
    monkeypatch.setenv("WOLF_WEEKEND_HEDGE", "1")
    monkeypatch.setenv("WOLF_WH_FILE", str(tmp_path / "wh.json"))
    today, prev = "20260821", "20260820"
    tb = _bars(today, [100] * 48)          # 今日缩量（总量小）
    yb = _bars(prev, [200] * 48)           # 昨日更大
    cal = ["20260820", "20260821", "20260824"]
    r = WH.run(bars=tb + yb, trade_days=cal, today8=today, hhmm="14:31")
    assert r["ok"] is True and r["prev_day"] == prev
    assert r["shrink_ratio"] is not None and r["shrink_ratio"] < 1.0
    assert r["active"] is True
    saved = json.loads((tmp_path / "wh.json").read_text(encoding="utf-8"))
    assert saved["active"] is True and saved["as_of"] == today


def test_run_not_saved_when_disabled(monkeypatch, tmp_path):
    f = tmp_path / "wh2.json"
    monkeypatch.setenv("WOLF_WH_FILE", str(f))
    monkeypatch.setenv("WOLF_WEEKEND_HEDGE", "0")
    WH.run(bars=_bars("20260821", [100] * 48), trade_days=["20260821", "20260824"],
           today8="20260821", hhmm="14:31")
    assert not f.exists()


# ── 假开关防护 ───────────────────────────────────────────────────────
def test_directive_empty_when_disabled(monkeypatch, tmp_path):
    p = tmp_path / "wh3.json"
    p.write_text(json.dumps({"ok": True, "directive": "泄漏"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("WOLF_WH_FILE", str(p))
    monkeypatch.setenv("WOLF_WEEKEND_HEDGE", "0")
    assert WH.directive() == ""


def test_directive_when_enabled(monkeypatch, tmp_path):
    p = tmp_path / "wh4.json"
    p.write_text(json.dumps({"ok": True, "directive": "周末避险提示"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("WOLF_WH_FILE", str(p))
    monkeypatch.setenv("WOLF_WEEKEND_HEDGE", "1")
    assert WH.directive() == "周末避险提示"

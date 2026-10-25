# -*- coding: utf-8 -*-
"""decision_window 时间窗口协议测试。"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services import decision_window as W  # noqa: E402


def _t(hm):
    return datetime.strptime("2026-01-14 " + hm, "%Y-%m-%d %H:%M")


def test_phase_boundaries():
    assert W.phase_at(_t("09:20"))["phase"] == "pre_open"
    assert W.phase_at(_t("10:00"))["phase"] == "intraday"
    assert W.phase_at(_t("13:15"))["phase"] == "intraday_blocked"
    assert W.phase_at(_t("14:50"))["phase"] == "tail_preview"
    assert W.phase_at(_t("14:55"))["phase"] == "tail_confirm"
    assert W.phase_at(_t("15:00"))["phase"] == "close"
    assert W.phase_at(_t("15:10"))["phase"] == "post_close"


def test_same_day_capability():
    assert W.phase_at(_t("14:50"))["same_day_executable"] is False
    assert W.phase_at(_t("14:55"))["same_day_executable"] is True
    assert W.phase_at(_t("15:00"))["same_day_executable"] is False
    assert W.phase_at(_t("13:15"))["same_day_executable"] is False


def test_should_ask_agent():
    assert W.should_ask_agent("close_break", "tail_preview") is False
    assert W.should_ask_agent("close_break", "tail_confirm") is True
    assert W.should_ask_agent("close_break", "close") is True
    assert W.should_ask_agent("close_break", "intraday_blocked") is False
    assert W.should_ask_agent("intraday_3x15", "intraday") is True
    assert W.should_ask_agent("volume_fast", "intraday") is True


def test_close_mode_windows():
    d = W.window_for_mode("close_break")
    phases = [x["phase"] for x in d["decision_windows"]]
    assert phases == ["tail_preview", "tail_confirm", "close"]
    assert d["decision_windows"][0]["ask_agent"] is False
    assert d["decision_windows"][1]["action"] == "same_day_tail"
    assert d["decision_windows"][2]["action"] == "next_day_if_not_tailed"

# -*- coding: utf-8 -*-
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "app" / "services"))


def test_trigger_ok():
    from app.services.wolf_low_position_line import _trigger
    t = _trigger({"current": 41.9, "pre_close": 38.85, "open": 39.59, "low": 38.55, "average": 40.6})
    assert t and t["ok"] is True


def test_trigger_rejects_weak():
    from app.services.wolf_low_position_line import _trigger
    t = _trigger({"current": 36.0, "pre_close": 38.0, "open": 36.2, "low": 35.8, "average": 37.0})
    assert t and t["ok"] is False


def test_candidates_20260812():
    from app.services.wolf_low_position_line import _build_candidates
    c = _build_candidates("20260812")
    assert c["can_do"] is True
    assert c["status"] == "ready"
    assert "mainline_not_sideways" not in (c.get("reasons") or [])
    codes = [s["code"] for s in c["stocks"]]
    assert "301382.SZ" in codes          # 蜂助手
    # 默认去掉 r60，改由入场日盘口 D 过滤；行云科技应保留在候选里
    assert "300209.SZ" in codes

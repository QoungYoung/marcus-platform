# -*- coding: utf-8 -*-
"""闸门异常报警 + 默认不放行（`WOLF_GATE_FAIL_CLOSED`）——账本 §9.117。

用户要求（2026-09-25）：「像这种 bug 类的能不能直接抛到日志里，我们复盘就能看到……
而不是放行？」背景：HPV 低吸路径判定用错变量名 ⇒ NameError 被 except 吞 ⇒ 静默 fail-open 102~103 行 ✗
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))


def _reload(**env):
    import app.services.gate_alarm as ga
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(ga)


def test_default_is_old_behavior():
    """库内默认 0 ⇒ 异常仍然放行（逐字旧行为，生产零影响）。"""
    ga = _reload(WOLF_GATE_FAIL_CLOSED=None)
    assert ga.fail_closed_on() is False
    assert ga.alarm("t", ValueError("x"), fail_closed=True) is False


def test_switch_on_blocks_and_records():
    """打开 ⇒ 返回 True（拦），且**落盘** JSONL + stderr 打 [GATE-ALARM]（复盘可见）。"""
    with tempfile.TemporaryDirectory() as d:
        ga = _reload(WOLF_GATE_FAIL_CLOSED="1", DATA_DIR=d)
        assert ga.fail_closed_on() is True
        assert ga.alarm("HPV_低吸路径:SZ000001:custom_m5dump", ValueError("boom"),
                        {"symbol": "SZ000001"}, fail_closed=True) is True
        recs = [json.loads(l) for l in open(os.path.join(d, "gate_failures.jsonl"), encoding="utf-8")]
        assert len(recs) == 1
        assert recs[0]["where"].startswith("HPV_低吸路径") and recs[0]["exc_type"] == "ValueError"


def test_fail_closed_false_still_allows():
    """对"放行才安全"的闸门（如取不到数据就不停做T）⇒ 传 fail_closed=False 永远放行。"""
    ga = _reload(WOLF_GATE_FAIL_CLOSED="1", DATA_DIR=tempfile.mkdtemp())
    assert ga.alarm("G8", RuntimeError("no data"), fail_closed=False) is False


def test_wiring_in_monitor():
    """接线：HPV 低吸路径的 except 必须走 gate_alarm（且异常时可拦）。"""
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = src.index("HPV 位置闸覆盖") if "HPV 位置闸覆盖" in src else 0
    seg = src[i:i + 3000]
    assert "gate_alarm" in seg
    assert "GATE-FAIL-CLOSED" in seg
    assert 'os.getenv("WOLF_GATE_FAIL_CLOSED", "0")' in open(
        os.path.join(_ROOT, "backend", "app", "services", "gate_alarm.py"), encoding="utf-8").read()

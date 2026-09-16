# -*- coding: utf-8 -*-
"""EOD 就绪闸的「探针日」语义 —— 盘前任务不许探当天（2026-09-16 修）。

**事故**：`daily_decision_am`（08:25）沿用 `gate(d8)` 默认探**当天**日线
（`pro.daily(trade_date=20260916)`）→ 开盘前当天日线必然是 0 行 → 生产 09-15 / 09-16
两天各白等 6×300s 后 rc=2 → **AM 决策对象从未产出**，盘中腿一直回退用前一天的对象。

本文件锁死修复语义：
  · `probe="self"`（默认，盘后）= 探 date8 当天 —— 行为不变（回归保护）
  · `probe="last-closed"`（盘前）= 探 date8 之前最近一个交易日
  · `probe="none"` = 只做交易日判断，不探就绪
  · 显式 `YYYYMMDD` 也可
并覆盖 job 侧的命令行/环境变量接线（`--eod-probe`）。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_eod as E  # noqa: E402


def _load_job():
    spec = importlib.util.spec_from_file_location(
        "job_daily_decision", REPO_ROOT / "jobs" / "daily_decision.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JOB = _load_job()


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    """单测永不真的 sleep。"""
    monkeypatch.setattr(E.time, "sleep", lambda *a, **k: None)
    monkeypatch.setenv("WOLF_EOD_TRIES", "1")
    monkeypatch.delenv("WOLF_EOD_PROBE", raising=False)
    yield


def _stub_ready(monkeypatch, ready_dates):
    """eod_ready 打桩：只有 ready_dates 里的日期算就绪，并记录被探的日期。"""
    seen = []

    def _f(d=None):
        seen.append(d)
        return d in ready_dates

    monkeypatch.setattr(E, "eod_ready", _f)
    return seen


# ── 探针日语义 ───────────────────────────────────────────────────────
def test_default_probe_is_object_date(monkeypatch):
    """回归保护：盘后任务的默认行为必须仍是「探当天」。"""
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
    seen = _stub_ready(monkeypatch, {"20260916"})
    assert E.gate("20260916") == 0
    assert seen == ["20260916"]


def test_last_closed_probe_uses_previous_trade_day(monkeypatch):
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
    monkeypatch.setattr(E, "prev_trade_day", lambda d=None: "20260915")
    seen = _stub_ready(monkeypatch, {"20260915"})
    assert E.gate("20260916", probe="last-closed") == 0
    assert seen == ["20260915"]


def test_premarket_does_not_block_on_today_missing(monkeypatch):
    """复现事故 + 验证修复：当天 0 行、昨天就绪时，盘前必须放行。"""
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
    monkeypatch.setattr(E, "prev_trade_day", lambda d=None: "20260915")
    _stub_ready(monkeypatch, {"20260915"})            # 20260916 未就绪（当天日线还没生成）
    assert E.gate("20260916", probe="last-closed") == 0
    assert E.gate("20260916") == 2                    # 旧默认（探当天）仍是"未就绪"


def test_probe_none_skips_readiness(monkeypatch):
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
    seen = _stub_ready(monkeypatch, set())
    assert E.gate("20260916", probe="none") == 0
    assert seen == []


def test_probe_none_still_skips_non_trade_day(monkeypatch):
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: False)
    assert E.gate("20260912", probe="none") == 1


def test_probe_explicit_date(monkeypatch):
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
    seen = _stub_ready(monkeypatch, {"20260910"})
    assert E.gate("20260916", probe="20260910") == 0
    assert seen == ["20260910"]


def test_resolve_probe_unknown_falls_back_to_self(monkeypatch):
    assert E.resolve_probe_date("whatever", "20260916") == "20260916"
    assert E.resolve_probe_date(None, "20260916") == "20260916"


# ── prev_trade_day ──────────────────────────────────────────────────
def test_prev_trade_day_from_calendar(monkeypatch):
    monkeypatch.setattr("app.services.t_backtest_data.resolve_trade_days",
                        lambda a, b: ["20260910", "20260911", "20260914", "20260915", "20260916"])
    assert E.prev_trade_day("20260916") == "20260915"
    assert E.prev_trade_day("20260914") == "20260911"      # 周一 → 周五


def test_prev_trade_day_fallback_weekday_when_calendar_down(monkeypatch):
    monkeypatch.setattr("app.services.t_backtest_data.resolve_trade_days",
                        lambda a, b: (_ for _ in ()).throw(RuntimeError("no cal")))
    assert E.prev_trade_day("20260914") == "20260911"      # 周一 → 退回周五
    assert E.prev_trade_day("20260916") == "20260915"


# ── job 侧接线（--eod-probe / WOLF_EOD_PROBE） ───────────────────────
def _run_job(monkeypatch, argv, env=None):
    from app.services import daily_decision as DD
    calls = {}

    def _gate(d8=None, probe="self"):
        calls["gate"] = {"d8": d8, "probe": probe}
        return 0

    def _run(d8=None, save=True):
        calls["run"] = {"d8": d8, "save": save}
        return {"ok": True}

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(DD, "enabled", lambda: True)
    monkeypatch.setattr(DD, "run", _run)
    monkeypatch.setattr(E, "gate", _gate)
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    rc = JOB.main()
    return rc, calls


def test_job_passes_eod_probe_flag(monkeypatch):
    monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
    rc, calls = _run_job(monkeypatch, ["daily_decision.py", "--eod-probe", "last-closed"])
    assert rc == 0
    assert calls["gate"]["probe"] == "last-closed"


def test_job_probe_defaults_to_self(monkeypatch):
    rc, calls = _run_job(monkeypatch, ["daily_decision.py"])
    assert rc == 0
    assert calls["gate"]["probe"] == "self"


def test_job_probe_from_env(monkeypatch):
    rc, calls = _run_job(monkeypatch, ["daily_decision.py"], env={"WOLF_EOD_PROBE": "none"})
    assert rc == 0
    assert calls["gate"]["probe"] == "none"


def test_job_flag_beats_env(monkeypatch):
    rc, calls = _run_job(monkeypatch, ["daily_decision.py", "--eod-probe", "self"],
                         env={"WOLF_EOD_PROBE": "none"})
    assert rc == 0
    assert calls["gate"]["probe"] == "self"


# ── 配置一致性：盘前任务必须带 --eod-probe last-closed ────────────────
def test_config_am_task_declares_last_closed_probe():
    txt = (REPO_ROOT / "config" / "tasks.yaml").read_text(encoding="utf-8")
    blk = txt.split("- id: daily_decision_am", 1)[1].split("\n- id: ", 1)[0]
    assert "--eod-probe" in blk and "last-closed" in blk, \
        "盘前 08:25 任务必须显式 --eod-probe last-closed（否则探当天日线必然未就绪）"
    pm = txt.split("- id: daily_decision\n", 1)[1].split("\n- id: ", 1)[0]
    assert "--eod-probe" not in pm, "盘后任务保持默认探当天，不要加盘前探针参数"

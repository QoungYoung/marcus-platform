# -*- coding: utf-8 -*-
"""G1 决策对象准入接线（t_gateway._decision_gate）单测 —— 2026-09-12。

**这是"判据先于成交"真正生效的地方**，所以三条硬约束必须钉死：
  ① 只拦买入（卖出/止损永不拦）
  ② WOLF_DECISION_GATE=0（默认）时完全不生效
  ③ shadow 模式只记录不拦；机制自身异常时**放行**（不能变成新的静默停摆源）
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import daily_decision as DD  # noqa: E402
from app.services import t_gateway as GW  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("WOLF_DECISION_GATE", "WOLF_DECISION_GATE_SHADOW", "WOLF_DECISION_GATE_MISSING",
              "WOLF_DECISION_MAX_AGE_DAYS", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(DD, "_load_pg", lambda d8: None)      # 单测不碰库
    monkeypatch.setattr(DD, "_save_pg", lambda obj: {"ok": False, "reason": "test"})
    monkeypatch.setattr(DD, "_latest_pg_at_or_before", lambda d8: None)   # 陈旧回退也别碰库
    DD._ALLOW_CACHE.update({"at": 0.0, "key": "", "value": None})   # 清准入缓存，避免跨用例泄漏
    yield


def _write_obj(d8, allowed, blockers=None):
    d = DD.decision_dir()
    Path(d).mkdir(parents=True, exist_ok=True)
    obj = DD.build(d8, gate={"confirmed": ["半导体"]}, wave={"operation": "build"}, tiers={}, picks=None,
                   gates={"G10_volume_gate": {"fake_breakout_risk": True}} if not allowed else {})
    Path(DD.path_for(d8)).write_text(
        __import__("json").dumps(obj, ensure_ascii=False), encoding="utf-8")
    return obj


def test_gate_off_never_blocks(monkeypatch):
    """默认关：无论有没有对象都放行（保证新机制不改变现状）。"""
    assert GW._decision_gate("SH600519") is None


def test_gate_on_blocks_buy_without_object(monkeypatch):
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    why = GW._decision_gate("SH600519")
    assert why and "准入拒绝" in why and "无可用决策对象" in why


def test_gate_on_blocks_buy_when_l5_blocked(monkeypatch):
    import datetime as _dt
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    today = _dt.date.today().strftime("%Y%m%d")
    _write_obj(today, allowed=False)                      # L5 被 G10 破位拦
    why = GW._decision_gate("SH600519")
    assert why and "诱多" in why


def test_gate_on_allows_when_l5_allows(monkeypatch):
    import datetime as _dt
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    today = _dt.date.today().strftime("%Y%m%d")
    _write_obj(today, allowed=True)
    assert GW._decision_gate("SH600519") is None


def test_shadow_mode_logs_but_allows(monkeypatch, tmp_path):
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    monkeypatch.setenv("WOLF_DECISION_GATE_SHADOW", "1")
    assert GW._decision_gate("SH600519") is None          # 不拦
    log = tmp_path / "decision_gate_log.jsonl"
    assert log.is_file() and "shadow" in log.read_text(encoding="utf-8")


def test_missing_policy_allow(monkeypatch):
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    monkeypatch.setenv("WOLF_DECISION_GATE_MISSING", "allow")
    assert GW._decision_gate("SH600519") is None


def test_stale_object_within_max_age_is_used(monkeypatch):
    """周一场次只有周五的对象也要能用（跨周末），并在原因里标陈旧天数。"""
    import datetime as _dt
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    today = _dt.date.today()
    old = (today - _dt.timedelta(days=2)).strftime("%Y%m%d")
    _write_obj(old, allowed=False)
    why = GW._decision_gate("SH600519")
    assert why and "陈旧" in why


def test_exception_in_gate_allows(monkeypatch):
    """准入自身异常必须放行（否则新机制会变成新的静默停摆源）。"""
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    monkeypatch.setattr(DD, "entry_allowed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert GW._decision_gate("SH600519") is None


def test_entry_allowed_cached_reuses_result(monkeypatch):
    """60s 缓存：同一进程内不重复查库/读文件（决策一天只变两次）。"""
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    calls = {"n": 0}
    real = DD.entry_allowed
    def counting(d8=None):
        calls["n"] += 1
        return real(d8)
    monkeypatch.setattr(DD, "entry_allowed", counting)
    DD._ALLOW_CACHE.update({"at": 0.0, "key": "", "value": None})
    DD.entry_allowed_cached()
    DD.entry_allowed_cached()
    assert calls["n"] == 1
    DD._ALLOW_CACHE.update({"at": 0.0, "key": "", "value": None})   # 清理，避免污染其它用例

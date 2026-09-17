# -*- coding: utf-8 -*-
"""时钟同源单测（2026-09-17，P1 缺陷 3：DB 真钟 vs Python 进程钟）。

## 缺陷（修复前）

`t_triggers.created_at` 由**表默认值 `now()`（DB 服务器钟）**写入，`claimed_at = now()` 也是 DB 钟；
而 `t_db.claim_pending_trigger` 的孤儿单清扫 cutoff、`t_gateway.classify_escalation` 的
`datetime.now() - claimed_at` 用的是 **Python 进程钟**。生产里两钟一致 ⇒ 看不出问题；
回测/重放里 `jobs/bt_run_pinned.pin_clock()` 把 Python 钉到 as-of（2026-03）而 DB 是真钟（2026-09）：
  · 孤儿单清扫恒不触发（实测 2,512 条 pending 永不过期，生产语义是 300s 后 cancelled）；
  · 人工确认超时算成**负数** → 永不超时，本该转 human 的单仍走 agent。

## 修法（同一个比较，两侧必须同一钟源）

| 比较 | 钟源 | 回退开关 |
| --- | --- | --- |
| `created_at`（DB 默认 now()）↔ 清扫 cutoff | **DB**（`now() - make_interval(secs => :timeout)`） | `T_DB_SWEEP_CLOCK=py` |
| `claimed_at` ↔ 网关超时 | **Python**（`claimed_at = :claimed_at` 传 `datetime.now()`） | `T_DB_CLAIMED_AT_CLOCK=db` |

网关另加一条 fail-closed 兜底：claimed_at 比进程钟"未来"超过容忍秒数（跨钟源遗留行）→ 按已超时转 human
（`T_GATE_CROSS_CLOCK_TIMEOUT=0` 可关）。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_db as t_db          # noqa: E402
import app.services.t_gateway as TG       # noqa: E402

# 回测 as-of：Python 钉在 2026-03-16（与 bt_run_pinned.pin_clock 同形），DB 真钟是 2026-09
PINNED = datetime(2026, 3, 16, 9, 15, 0)
DB_CLOCK_ROW = datetime(2026, 9, 17, 7, 30, 0)      # 只有 DB now() 才写得出的"未来"时间


class _PinnedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return PINNED


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row=None):
        self.calls = []
        self.row = row
        self.committed = False
        self.closed = False

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(self.row)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("T_DB_SWEEP_CLOCK", raising=False)
    monkeypatch.delenv("T_DB_CLAIMED_AT_CLOCK", raising=False)
    monkeypatch.delenv("T_GATE_CROSS_CLOCK_TIMEOUT", raising=False)
    monkeypatch.delenv("T_GATE_CROSS_CLOCK_TOL_S", raising=False)


def _claim(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sess = _FakeSession()
    monkeypatch.setattr(t_db, "SessionLocal", lambda: sess)
    monkeypatch.setattr(t_db, "datetime", _PinnedDT)
    out = t_db.claim_pending_trigger("worker-1", timeout_seconds=300)
    sweep = [c for c in sess.calls if "orphan_timeout" in c[0]]
    claim = [c for c in sess.calls if "status = 'claimed'" in c[0]]
    assert len(sweep) == 1 and len(claim) == 1, sess.calls
    return sweep[0], claim[0], out


# ────────────────────────── 清扫：与 created_at 同源（DB 钟）──────────────────────────

def test_orphan_sweep_uses_db_clock_like_created_at(monkeypatch):
    """默认：cutoff 与 created_at 同源（都在 DB 侧算）——回测里 Python 与 DB 差数月也不会失配。"""
    (sql, params), _claim_sql, _ = _claim(monkeypatch)
    assert "created_at < now() - make_interval(secs => :timeout)" in sql
    assert params == {"timeout": 300.0}
    assert "cutoff" not in params, "又把 Python cutoff 拿去比 DB created_at 了"


def test_orphan_sweep_legacy_python_cutoff(monkeypatch):
    """回退开关：T_DB_SWEEP_CLOCK=py → 修复前行为（Python cutoff 比 DB created_at）。"""
    (sql, params), _claim_sql, _ = _claim(monkeypatch, T_DB_SWEEP_CLOCK="py")
    assert "created_at < :cutoff" in sql
    assert params["cutoff"] == PINNED - timedelta(seconds=300)


# ────────────────────────── 认领：claimed_at 用 Python 钟 ──────────────────────────

def test_claim_writes_python_clock_not_db_now(monkeypatch):
    """claimed_at 必须等于"调用时 Python 钟"（回测里 = as-of，而不是 DB 真钟 2026-09）。"""
    _sweep, (sql, params), _ = _claim(monkeypatch)
    assert "claimed_at = :claimed_at" in sql
    assert "claimed_at = now()" not in sql
    assert params["claimed_at"] == PINNED
    assert params["claimed_at"] != DB_CLOCK_ROW


def test_claim_legacy_db_clock_switch(monkeypatch):
    """回退开关：T_DB_CLAIMED_AT_CLOCK=db → 回到 SQL now()。"""
    _sweep, (sql, params), _ = _claim(monkeypatch, T_DB_CLAIMED_AT_CLOCK="db")
    assert "claimed_at = now()" in sql
    assert "claimed_at" not in params


def test_claim_is_single_clock_source_per_call(monkeypatch):
    """同一次 claim 内只取一次时：清扫 cutoff（py 模式）与 claimed_at 用同一个 now。"""
    (sweep_sql, sweep_params), (_c, claim_params), _ = _claim(monkeypatch, T_DB_SWEEP_CLOCK="py")
    assert claim_params["claimed_at"] - sweep_params["cutoff"] == timedelta(seconds=300)


# ────────────────────────── 网关：人工确认超时（同源后才有意义）──────────────────────────

def _escalate(claimed_at):
    with mock.patch.object(TG.t_db, "get_risk_state", return_value={}), \
         mock.patch.object(TG, "get_sellable_ledger", return_value={"SH600000": {}}), \
         mock.patch.object(TG, "_daily_pnl_pct", return_value=0.0), \
         mock.patch.object(TG, "datetime", _PinnedDT):   # 与 bt_run_pinned.pin_clock 同形
        return TG.classify_escalation(
            "SH600000", "sell", {"status": "claimed", "claimed_at": claimed_at}, "ACTIVE")


def test_gateway_timeout_fires_when_claimed_at_is_python_clock(monkeypatch):
    """claimed_at = Python 钟（修复后的写入口径）→ 超 2min 正常判定为孤儿单。"""
    mode, reason = _escalate(PINNED - timedelta(minutes=3))
    assert mode == "human" and "孤儿单超时" in reason
    # 字符串形态（部分驱动返回 str）同样能解析
    mode, reason = _escalate((PINNED - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"))
    assert mode == "human" and "孤儿单超时" in reason


def test_gateway_timeout_does_not_fire_within_window(monkeypatch):
    mode, reason = _escalate(PINNED - timedelta(seconds=30))
    assert (mode, reason) == ("auto", "")


def test_gateway_cross_clock_legacy_row_fails_closed(monkeypatch):
    """遗留行（claimed_at 由 DB now() 写入 = "未来时间"）→ 修复前差值恒负 ⇒ 永不超时、仍走 agent。

    现在按已超时转 human（fail-closed）：宁可人工确认，也不放行一笔账实不明的单。
    """
    mode, reason = _escalate(DB_CLOCK_ROW)
    assert mode == "human" and "不同源" in reason


def test_gateway_cross_clock_guard_can_be_disabled(monkeypatch):
    monkeypatch.setenv("T_GATE_CROSS_CLOCK_TIMEOUT", "0")
    assert _escalate(DB_CLOCK_ROW) == ("auto", "")


def test_gateway_ignores_missing_or_unparsable_claimed_at():
    assert _escalate(None) == ("auto", "")
    assert _escalate("not-a-timestamp") == ("auto", "")
    assert _escalate(object()) == ("auto", "")


def test_claimed_age_seconds_handles_tz_aware():
    """psycopg 若返回带时区的值：先剥 tzinfo 再比（列是 timestamp without time zone）。"""
    from datetime import timezone
    aware = (PINNED - timedelta(minutes=5)).replace(tzinfo=timezone.utc)
    assert TG._claimed_age_seconds(aware, PINNED) == 300.0
    assert TG._claimed_age_seconds(None, PINNED) is None
    assert TG._claimed_age_seconds(object(), PINNED) is None


def test_claimed_age_seconds_survives_pinned_datetime_class(monkeypatch):
    """钉钟回归：`datetime.datetime` 被换成子类（bt_run_pinned.pin_clock）后仍要能判定年龄。

    旧写法 isinstance(claimed_dt, datetime) 在"模块于钉钟之后 import"时会判假 → 超时被静默跳过。
    """
    monkeypatch.setattr(TG, "datetime", _PinnedDT)
    assert TG._claimed_age_seconds(PINNED - timedelta(minutes=3), PINNED) == 180.0

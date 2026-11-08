# -*- coding: utf-8 -*-
"""回测 agent 消费循环的**认领范围口径**（2026-09-19 用户："上次回测怎么正常呢，我们也没改什么东西啊"）。

## 缺陷（当天引入，当天发现）

`BtAgentLoop._claim_pending` 原判据：`created_at >= to_timestamp(run_since)`，`run_since = real_epoch_now()`
（**真实**墙钟）。它在"created_at 由 DB 真钟写入"时成立；当天把 `t_triggers.created_at` 统一到
**Python 钉钟**（回放日）后，回放里 `created_at(2026-01) < to_timestamp(真实 epoch 2026-09)`
⇒ **一条 pending 都认领不到** ⇒ wolf_* 触发全积压成 pending、AI 裁决链整条哑火
（实测 0105→0130 只有 blocked/pending，没有一处 AI 裁决的 executed/await_retry/cancelled）。

## 修法

认领范围改用 **id 水位**（`id > 本次运行开始时的最大触发 id`）：语义与 run_since 相同
（"本次运行之后写入的行"），但**不依赖任何钟**。`BT_AGENT_CLAIM_FILTER=time` 回退旧口径。
同时 `claimed_at` 由 `now()`（DB 钟）改传 `datetime.now()`（Python 钟），与网关超时判定同源。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import bt_agent_loop as BAL  # noqa: E402


class _Res:
    def __init__(self, row=None, scalar=None):
        self._row, self._scalar = row, scalar

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar(self):
        return self._scalar


class _Sess:
    def __init__(self):
        self.calls = []
        self.committed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if "MAX(id)" in sql:
            return _Res(scalar=1234)
        return _Res(row={"id": 1235, "account_id": "drabjan10", "symbol": "SH603068",
                         "event_type": "wolf_zheng_t_buy", "status": "pending"})

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _run(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import types
    sess = _Sess()
    mod = types.ModuleType("app.database")
    mod.SessionLocal = lambda: sess
    monkeypatch.setitem(sys.modules, "app.database", mod)
    loop = BAL.BtAgentLoop(account="drabjan10", consumer="bt-fallback", verbose=False)
    row = loop._claim_pending()
    return loop, sess, row


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("BT_AGENT_CLAIM_FILTER", raising=False)


def test_默认用id水位_而不是真实epoch(monkeypatch):
    loop, sess, row = _run(monkeypatch)
    claim = [c for c in sess.calls if "status = 'claimed'" in c[0]]
    assert len(claim) == 1, sess.calls
    sql, params = claim[0]
    assert "AND id > :wm" in sql
    assert "to_timestamp(:since)" not in sql, "又用真实 epoch 比钉钟 created_at 了"
    assert params["wm"] == 1234 and row and row["id"] == 1235


def test_claimed_at用Python钟(monkeypatch):
    _loop, sess, _row = _run(monkeypatch)
    sql, params = [c for c in sess.calls if "status = 'claimed'" in c[0]][0]
    assert "claimed_at = :claimed_at" in sql and "claimed_at = now()" not in sql
    assert isinstance(params["claimed_at"], datetime)


def test_回退开关_旧口径(monkeypatch):
    loop, sess, _row = _run(monkeypatch, BT_AGENT_CLAIM_FILTER="time")
    sql, params = [c for c in sess.calls if "status = 'claimed'" in c[0]][0]
    assert "created_at >= to_timestamp(:since)" in sql and "id > :wm" not in sql
    assert params["since"] == pytest.approx(float(loop.run_since))


def test_水位只取一次(monkeypatch):
    loop, sess, _row = _run(monkeypatch)
    loop._claim_pending()
    loop._claim_pending()
    assert len([c for c in sess.calls if "MAX(id)" in c[0]]) == 1

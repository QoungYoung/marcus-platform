# -*- coding: utf-8 -*-
"""认领**水位取值时机**的回归测试（2026-09-20 夜间排查"不合理亏损"时发现）。

## 缺陷（我 09-19 引入的 id 水位修，自己埋的坑）

把认领判据从"真实 epoch"改成 **id 水位**后，水位是**懒取**的（第一次 `_claim_pending()` 才
`SELECT MAX(id)`），而第一次认领发生在开盘后第一个 poll（实测 **09:40**）——此刻当日 **09:35 那一批**
规则触发已经写库 ⇒ 水位把它们一起框在外面 ⇒ **每个回放日的 09:35 批次永远不会被 AI 裁决**。

**实测证据**（jan10）：`t_triggers` 里 111 条 `pending` **全部** `created_at=09:35`、`claimed_at` 全空；
按分钟统计里只有 09:35 这一档是 `pending+blocked`、**零 executed/cancelled/await_retry**；
0108 的 09:35 批次 id 为 186385–186396，而当日第一条 AI 裁决是 **#186399**（水位恰在 09:40 poll 时取，
= 186398）。jan11 117 条、jan12 前 14 天 78 条同型。

## 修法

`snapshot_watermark()` 在 **构造时**取水位（`bt_prod_run` 在 bar 循环**之前**构造 `BtAgentLoop`
⇒ 水位 = 上一日最后一行的 id ⇒ 当日全部触发都在水位之上）。开关 `BT_AGENT_CLAIM_WM`：
`first-poll` = 旧行为（默认，库内零变化）；`eager` = 构造时取（回测 pins 打开）。
生产 `fallback_poll_loop` 不使用本模块 ⇒ 两种取值都**零生产影响**。
"""
import sys
import types
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
    """假会话：`MAX(id)` 返回 `self.maxid`（可随时改，模拟"构造之后又写入新触发"）。"""

    def __init__(self, maxid=1234):
        self.maxid = maxid
        self.calls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if "MAX(id)" in sql:
            return _Res(scalar=self.maxid)
        return _Res(row={"id": self.maxid + 1, "account_id": "drabjan10", "symbol": "SH603068",
                         "event_type": "wolf_zheng_t_buy", "status": "pending"})

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _mk(monkeypatch, maxid=1234, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sess = _Sess(maxid=maxid)
    mod = types.ModuleType("app.database")
    mod.SessionLocal = lambda: sess
    monkeypatch.setitem(sys.modules, "app.database", mod)
    return sess


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("BT_AGENT_CLAIM_FILTER", "BT_AGENT_CLAIM_WM"):
        monkeypatch.delenv(k, raising=False)


def test_默认不取水位_构造后仍为None(monkeypatch):
    """库内默认必须是旧行为：不动任何既有回测口径。"""
    sess = _mk(monkeypatch)
    loop = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    assert loop.claim_wm == "first-poll"
    assert loop._wm is None
    assert not [c for c in sess.calls if "MAX(id)" in c[0]], "默认不该在构造时查库"


def test_eager_构造时即取水位(monkeypatch):
    sess = _mk(monkeypatch, BT_AGENT_CLAIM_WM="eager")
    loop = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    assert loop._wm == 1234
    assert len([c for c in sess.calls if "MAX(id)" in c[0]]) == 1


def test_eager_当日早盘批次不再被漏掉_核心回归(monkeypatch):
    """核心回归：**构造之后**写入的 09:35 批次必须在认领范围内。

    模拟真实顺序：构造 loop（水位=1234）→ 09:35 规则批写入（库内 MAX 变 1240）→ 09:40 首次认领。
    旧口径（first-poll）此时水位会取成 1240 ⇒ `id > 1240` ⇒ 1235–1240 那批触发永久 pending。
    """
    # 旧口径：确认 bug 存在
    sess = _mk(monkeypatch)
    loop_old = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    sess.maxid = 1240                       # 09:35 批次写库
    loop_old._claim_pending()
    sql_old, params_old = [c for c in sess.calls if "status = 'claimed'" in c[0]][0]
    assert params_old["wm"] == 1240, "旧口径的水位取晚了（这就是缺陷本身）"

    # 新口径：水位停在构造时
    sess2 = _mk(monkeypatch, BT_AGENT_CLAIM_WM="eager")
    loop_new = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    sess2.maxid = 1240
    loop_new._claim_pending()
    _sql, params_new = [c for c in sess2.calls if "status = 'claimed'" in c[0]][0]
    assert params_new["wm"] == 1234, "eager 口径下水位必须停在构造时"
    assert "AND id > :wm" in _sql


def test_snapshot幂等(monkeypatch):
    sess = _mk(monkeypatch, BT_AGENT_CLAIM_WM="eager")
    loop = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    loop.snapshot_watermark()
    loop._claim_pending()
    loop._claim_pending()
    assert len([c for c in sess.calls if "MAX(id)" in c[0]]) == 1


def test_all_pending时不取水位(monkeypatch):
    """`all_pending` 走生产全量认领（含孤儿单处置）⇒ 与水位无关，eager 也不该查库。"""
    sess = _mk(monkeypatch, BT_AGENT_CLAIM_WM="eager")
    loop = BAL.BtAgentLoop(account="drabjan10", all_pending=True, verbose=False)
    assert loop._wm is None
    assert not [c for c in sess.calls if "MAX(id)" in c[0]]


def test_time口径时eager不生效(monkeypatch):
    """`BT_AGENT_CLAIM_FILTER=time`（旧钟口径）下不该去取水位。"""
    sess = _mk(monkeypatch, BT_AGENT_CLAIM_WM="eager", BT_AGENT_CLAIM_FILTER="time")
    loop = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    assert loop._wm is None
    assert not [c for c in sess.calls if "MAX(id)" in c[0]]


def test_取水位失败不影响启动(monkeypatch):
    """DB 取不到（如单测无库）时必须降级为懒取，而不是把回测驱动搞崩。"""
    class _Boom(_Sess):
        def execute(self, stmt, params=None):
            if "MAX(id)" in str(stmt):
                raise RuntimeError("db down")
            return super().execute(stmt, params)

    sess = _Boom()
    mod = types.ModuleType("app.database")
    mod.SessionLocal = lambda: sess
    monkeypatch.setitem(sys.modules, "app.database", mod)
    monkeypatch.setenv("BT_AGENT_CLAIM_WM", "eager")
    loop = BAL.BtAgentLoop(account="drabjan10", verbose=False)
    assert loop._wm is None                # 不抛、可继续


def test_接线护栏_构造早于bar循环():
    """护栏：`bt_prod_run` 必须在 **bar 循环之前**构造 `BtAgentLoop`，否则水位又会被早盘批次污染。"""
    src = (REPO_ROOT / "jobs" / "bt_prod_run.py").read_text(encoding="utf-8")
    i_ctor = src.find("bt_agent_loop.BtAgentLoop(")
    i_loop = src.find("# ⑦ 逐 bar 驱动")
    assert i_ctor > 0 and i_loop > 0, "找不到构造点或 bar 循环标记（标记若改名请同步本测试）"
    assert i_ctor < i_loop, "BtAgentLoop 必须在 bar 循环之前构造（水位=上一日最后 id）"


def test_回测pins已打开eager():
    """口径表：回测覆盖层必须显式打开 `eager`（库内默认 first-poll ⇒ 不改库也能回退）。"""
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    assert "BT_AGENT_CLAIM_WM='eager'" in txt or 'BT_AGENT_CLAIM_WM="eager"' in txt
    assert (REPO_ROOT / "jobs" / "bt_agent_loop.py").read_text(
        encoding="utf-8").count('os.getenv("BT_AGENT_CLAIM_WM", "first-poll")') == 1

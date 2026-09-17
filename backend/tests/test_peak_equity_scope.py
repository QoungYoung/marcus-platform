# -*- coding: utf-8 -*-
"""peak_equity 键口径（按账户 / 按 run）单测（2026-09-17，P1 缺陷 7）。

## 缺陷（修复前）

`app/core/peak_equity.py` 用**全局单键** `peak_equity`，`position_tier_monitor._get_total_drawdown`
与 `trade_graph._read_portfolio` 都读写它 ⇒ 所有账户共用一条峰值、回放/克隆库还会继承
**别的时期/别的账户**的陈旧峰值。实测克隆库 `system_state.peak_equity = 1,897,740.27`
（2026-08-26 写入）对比当前 `total_asset = 232,167` → **回撤 87.8%**
⇒ 第 2 道门（总回撤 ≥5% 硬禁止）首轮就把所有加仓拦掉。

## 修法

键 = `peak_equity:acct:<account_id>[:run:<run_id>]`：
· 账户维度必给（`position_tier_monitor` 用 executor.account_id / `trade_graph` 用 'stock'）；
· run 维度取 `WOLF_PEAK_EQUITY_RUN`（回测/回放启动时设 → 每个 run 从零起算，不继承旧峰值）；
· 不传 account_id 的老调用方仍用旧全局键（向后兼容）；
· `WOLF_PEAK_EQUITY_INHERIT_LEGACY=1` 才回落读旧全局键（默认关：旧值已知跨账户污染）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.database as DB  # noqa: E402
import app.core.peak_equity as PE  # noqa: E402
import app.services.position_tier_monitor as M  # noqa: E402


# ────────────────────────── 假 ORM 会话（不连库）──────────────────────────

class _Row:
    """写穿到 store 的"ORM 行"：`row.value = x` 立刻反映到 store（模拟 session flush）。"""

    def __init__(self, store, key):
        self._store = store
        self.key = key

    @property
    def value(self):
        return self._store[self.key]

    @value.setter
    def value(self, v):
        self._store[self.key] = v

    @property
    def updated_at(self):
        return None

    @updated_at.setter
    def updated_at(self, v):
        pass


class _Query:
    def __init__(self, store):
        self._store = store
        self._key = None

    def filter(self, expr):
        try:
            self._key = expr.right.value          # SystemState.key == <literal>
        except AttributeError:
            self._key = None
        return self

    def first(self):
        if self._key in self._store:
            return _Row(self._store, self._key)
        return None


class _Session:
    def __init__(self, store):
        self._store = store
        self.rolled_back = False

    def query(self, model):
        return _Query(self._store)

    def add(self, obj):
        self._store[obj.key] = obj.value

    def commit(self):
        pass

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def store(monkeypatch):
    """把 peak_equity 的 DB 落成内存字典；预置生产克隆里的陈旧全局峰值。"""
    st = {"peak_equity": "1897740.27"}      # 2026-08-26 生产写入的全局峰值（污染源）
    monkeypatch.setattr(DB, "SessionLocal", lambda: _Session(st))
    monkeypatch.delenv("WOLF_PEAK_EQUITY_RUN", raising=False)
    monkeypatch.delenv("WOLF_PEAK_EQUITY_INHERIT_LEGACY", raising=False)
    return st


# ────────────────────────── 键口径 ──────────────────────────

def test_key_composition(monkeypatch):
    monkeypatch.delenv("WOLF_PEAK_EQUITY_RUN", raising=False)
    assert PE.peak_equity_key() == "peak_equity"                      # 老调用方：旧全局键
    assert PE.peak_equity_key("stock") == "peak_equity:acct:stock"
    assert PE.peak_equity_key("golden_pit") == "peak_equity:acct:golden_pit"
    assert PE.peak_equity_key("stock", "r1") == "peak_equity:acct:stock:run:r1"
    monkeypatch.setenv("WOLF_PEAK_EQUITY_RUN", "run-2026-03")
    assert PE.peak_equity_key("stock") == "peak_equity:acct:stock:run:run-2026-03"


def test_long_run_id_folded_and_key_len_ok(monkeypatch):
    """run id 可以是沙箱绝对路径 → 键长必须 ≤64（system_state.key = String(64)）且稳定。"""
    long_run = "/home/fengx/marcus-platform/data/_bt_pg/sandbox/2026-03-16/day-42"
    k1 = PE.peak_equity_key("stock", long_run)
    k2 = PE.peak_equity_key("stock", long_run)
    assert len(k1) <= 64, k1
    assert k1 == k2 and "~" in k1


# ────────────────────────── 账户 / run 隔离 ──────────────────────────

def test_accounts_are_isolated(store):
    PE.save_peak_equity(1_000_000.0, account_id="stock")
    PE.save_peak_equity(250_000.0, account_id="golden_pit")
    assert PE.load_peak_equity(account_id="stock") == 1_000_000.0
    assert PE.load_peak_equity(account_id="golden_pit") == 250_000.0
    # 一个账户创新高不影响另一个账户
    PE.save_peak_equity(1_200_000.0, account_id="stock")
    assert PE.load_peak_equity(account_id="golden_pit") == 250_000.0


def test_new_run_does_not_inherit_previous_peak(store, monkeypatch):
    monkeypatch.setenv("WOLF_PEAK_EQUITY_RUN", "run-A")
    PE.save_peak_equity(900_000.0, account_id="stock")
    assert PE.load_peak_equity(account_id="stock") == 900_000.0
    # 新 run：不继承 run-A 的峰值（= 从零起算，回退到 fallback）
    monkeypatch.setenv("WOLF_PEAK_EQUITY_RUN", "run-B")
    assert PE.load_peak_equity(fallback=232_167.0, account_id="stock") == 232_167.0


def test_stale_global_peak_is_not_inherited(store):
    """克隆库里的全局陈旧峰值（1,897,740.27）不再被账户键读到 —— 这正是 87.8% 假回撤的来源。"""
    assert PE.load_peak_equity(fallback=232_167.0, account_id="stock") == 232_167.0
    assert store["peak_equity"] == "1897740.27"          # 旧键原样留着（不删历史数据）


def test_legacy_inherit_switch(store, monkeypatch):
    """需要延续历史峰值的部署可显式开回落开关。"""
    monkeypatch.setenv("WOLF_PEAK_EQUITY_INHERIT_LEGACY", "1")
    assert PE.load_peak_equity(fallback=0.0, account_id="stock") == 1_897_740.27


def test_no_account_id_keeps_legacy_global_key(store):
    """老调用方（不传账户）行为不变：读写旧全局键。"""
    store.pop("peak_equity")            # 清掉预置的陈旧值，专测"老口径"写入路径
    PE.save_peak_equity(500_000.0)
    assert store["peak_equity"] == "500000.0"
    assert PE.load_peak_equity() == 500_000.0
    # 旧键只增不减的语义保持
    PE.save_peak_equity(400_000.0)
    assert PE.load_peak_equity() == 500_000.0


def test_save_is_monotonic_per_key(store):
    PE.save_peak_equity(100.0, account_id="stock")
    PE.save_peak_equity(50.0, account_id="stock")
    assert PE.load_peak_equity(account_id="stock") == 100.0


# ────────────────────────── 监控器层：87.8% 假回撤消失 ──────────────────────────

class _FakeExecutor:
    account_id = "stock"


def _drawdown(total_asset=232_167.0):
    mon = M.PositionTierMonitor.__new__(M.PositionTierMonitor)
    mon.executor = _FakeExecutor()
    mon._trend_cache = {}
    return mon._get_total_drawdown({"total_asset": total_asset, "initial_capital": 100_000.0})


def test_tier_monitor_drawdown_no_longer_phantom(store):
    """修复前：全局峰值 1,897,740 → 回撤 87.8% → 加仓首轮全被拦。修复后：按账户无记录 → 0。"""
    assert _drawdown(232_167.0) == 0.0
    assert "peak_equity:acct:stock" not in store      # 无账户键时不写（沿用 max(初始资金,当前权益) 基准）
    assert store["peak_equity"] == "1897740.27"       # 旧全局键不再被本路径读/改
    # 账户键一旦存在，创新高写的是**账户键**（而不是回头污染全局键）
    PE.save_peak_equity(200_000.0, account_id="stock")
    assert _drawdown(300_000.0) == 0.0
    assert store["peak_equity:acct:stock"] == "300000.0"
    assert store["peak_equity"] == "1897740.27"


def test_tier_monitor_drawdown_still_works_within_account(store):
    """同账户内的真回撤仍要拦：峰值 250,000 → 当前 232,167 = 7.1% 回撤。"""
    PE.save_peak_equity(250_000.0, account_id="stock")
    dd = _drawdown(232_167.0)
    assert dd == pytest.approx(1 - 232_167.0 / 250_000.0, rel=1e-6)
    assert dd >= 0.05        # 第 2 道门仍会 BLOCKED

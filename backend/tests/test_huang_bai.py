# -*- coding: utf-8 -*-
"""A4 指数级黄白线（2026-09-11）单测。

狼大原话（XLS 2025-04-15「我的买卖做T方法」条件 3）：
  「如果提前预判是**黄线高于白线**，意味着科技类 消费类这些主线会强于银保地券商这些指数标…
    这个时候**做T成功率高**；如果预判是**白线高于黄线**…这种时候一般**减少做T**」

口径：白线 = 上证指数涨跌幅；黄线 = 沪市个股等权平均涨跌幅（**代理**，非软件里那条线）。
取数走新浪全A（约 16s）→ 必须缓存 + fail-open，**不得阻塞或改变交易判断**。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_index_breadth as HB  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in ("WOLF_HUANG_BAI", "WOLF_HUANG_BAI_TTL", "WOLF_HUANG_BAI_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    HB._CACHE.update({"at": 0.0, "value": None})
    yield
    HB._CACHE.update({"at": 0.0, "value": None})


class TestClassify:
    def test_huang_when_equal_weight_stronger(self):
        """黄线在上 = 等权强于加权 → 做T成功率高。"""
        assert HB.classify(1.5) == "huang"

    def test_bai_when_weight_stronger(self):
        """白线在上 = 权重强于小票 → 减少做T。"""
        assert HB.classify(-0.67) == "bai"

    def test_intertwined_returns_none(self):
        """黄白线交织（他成文里专门提过的情形）→ 不判方向。"""
        assert HB.classify(0.02) is None
        assert HB.classify(-0.05) is None
        assert HB.classify(0.0) is None

    def test_none_safe(self):
        assert HB.classify(None) is None


class TestFetchAndCache:
    def test_computes_spread(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (-1.766, 5000))
        monkeypatch.setattr(HB, "_index_pct", lambda: -1.100)
        h = HB.huang_bai(force=True)
        assert h["spread"] == pytest.approx(-0.666)
        assert h["side"] == "bai" and h["n"] == 5000 and h["stale"] is False

    def test_cache_avoids_second_fetch(self, monkeypatch):
        calls = []
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (calls.append(1), (0.5, 10))[1])
        monkeypatch.setattr(HB, "_index_pct", lambda: 0.1)
        HB.huang_bai()
        HB.huang_bai()
        assert len(calls) == 1                      # TTL 内只取一次

    def test_force_bypasses_cache(self, monkeypatch):
        calls = []
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (calls.append(1), (0.5, 10))[1])
        monkeypatch.setattr(HB, "_index_pct", lambda: 0.1)
        HB.huang_bai(); HB.huang_bai(force=True)
        assert len(calls) == 2

    def test_failure_returns_last_good_stale(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (0.8, 10))
        monkeypatch.setattr(HB, "_index_pct", lambda: 0.1)
        first = HB.huang_bai(force=True)
        assert first["stale"] is False
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (None, 0))   # 取数失败
        second = HB.huang_bai(force=True)
        assert second["stale"] is True and second["spread"] == first["spread"]  # 用上次成功值

    def test_no_data_at_all_returns_none(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (None, 0))
        monkeypatch.setattr(HB, "_index_pct", lambda: None)
        assert HB.huang_bai(force=True) is None

    def test_switch_off(self, monkeypatch):
        monkeypatch.setenv("WOLF_HUANG_BAI", "0")
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (1.0, 10))
        assert HB.huang_bai(force=True) is None

    def test_snapshot_shape_is_flat_and_safe(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (None, 0))
        monkeypatch.setattr(HB, "_index_pct", lambda: None)
        s = HB.snapshot()
        assert set(s) == {"huang_bai_spread", "huang_bai_side", "huang_bai_equal",
                          "huang_bai_index", "huang_bai_stale"}
        assert all(v is None for v in s.values())

    def test_snapshot_values(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (1.2, 100))
        monkeypatch.setattr(HB, "_index_pct", lambda: 0.3)
        s = HB.snapshot()
        assert s["huang_bai_spread"] == pytest.approx(0.9)
        assert s["huang_bai_side"] == "huang"


class TestDirective:
    def test_bai_cites_wolf_and_says_reduce(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (-1.7, 100))
        monkeypatch.setattr(HB, "_index_pct", lambda: -1.1)
        d = HB.directive()
        assert "白线在上" in d and "减少做T" in d and "2025-04-15" in d

    def test_huang_says_high_success(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (1.7, 100))
        monkeypatch.setattr(HB, "_index_pct", lambda: 0.5)
        d = HB.directive()
        assert "黄线在上" in d and "成功率" in d

    def test_stale_marked(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (0.9, 10))
        monkeypatch.setattr(HB, "_index_pct", lambda: 0.1)
        HB.huang_bai(force=True)
        HB._CACHE["at"] = 0.0                       # 让缓存过期（directive 不走 force）
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (None, 0))
        assert "上次成功值" in HB.directive()

    def test_no_data_empty_string(self, monkeypatch):
        monkeypatch.setattr(HB, "_equal_weight_pct", lambda scope: (None, 0))
        monkeypatch.setattr(HB, "_index_pct", lambda: None)
        assert HB.directive() == ""

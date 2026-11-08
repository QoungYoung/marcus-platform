# -*- coding: utf-8 -*-
"""主营判定提速单测：落盘缓存 / 负缓存 / 并发预热（2026-09-19）。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "apps", "main_line") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "apps", "main_line"))

import theme_member_llm as TM   # noqa: E402


class _R:
    def __init__(self, reply):
        self._b = json.dumps({"reply": reply}).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fresh(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(TM, "ON", on)
    monkeypatch.setattr(TM, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(TM, "TIMEOUT", 1.0)
    TM._CACHE.clear()
    for k in TM._STATS:
        TM._STATS[k] = 0


def test_落盘缓存_跨进程复用(tmp_path, monkeypatch):
    calls = []

    def _open(req, timeout=None):
        calls.append(1)
        return _R("无关")
    monkeypatch.setattr(TM.urllib.request, "urlopen", _open)
    _fresh(tmp_path, monkeypatch)
    assert TM.is_member("SZ000007", ["2026中报首亏"], "AI/算力/科技") == (False, "无关")
    assert TM._STATS["llm"] == 1
    TM._CACHE.clear()                     # 模拟"新进程"：内存缓存空了，磁盘还在
    assert TM.is_member("SZ000007", ["2026中报首亏"], "AI/算力/科技") == (False, "无关")
    assert len(calls) == 1, calls         # 没有再打 dsh
    assert TM._STATS["disk_hit"] == 1
    assert list(tmp_path.glob("*.json"))


def test_负缓存_失败后不再等超时(tmp_path, monkeypatch):
    """2026-09-20 更新：单票调用现在带**退避重试**（WOLF_MEMBER_RETRY，默认 2）⇒
    首发失败后最多再打 2 次（共 3 次请求）才落负缓存；负缓存命中后不再外呼。"""
    calls = []

    def _boom(req, timeout=None):
        calls.append(1)
        raise TimeoutError("timed out")
    monkeypatch.setattr(TM.urllib.request, "urlopen", _boom)
    monkeypatch.setenv("WOLF_MEMBER_RETRY", "2")
    monkeypatch.setenv("WOLF_MEMBER_RETRY_BACKOFF", "0")      # 测试里不等退避
    _fresh(tmp_path, monkeypatch)
    assert TM.is_member("SZ000009", ["MSCI中国"], "AI/算力/科技")[0] is True     # fail-open
    assert TM._STATS["err"] == 1
    assert TM._STATS["retry"] == 2, "重试 2 次后才放弃"
    assert len(calls) == 3, "首发 1 次 + 重试 2 次"
    assert TM._STATS["warn"] >= 1, "失败必须留痕（不再静默）"
    TM._CACHE.clear()
    assert TM.is_member("SZ000009", ["MSCI中国"], "AI/算力/科技") == (True, "neg_cache")
    assert len(calls) == 3                                        # 负缓存命中，没有第二次外呼
    assert TM._STATS["neg_hit"] == 1


def test_并发预热(tmp_path, monkeypatch):
    calls = []

    def _open(req, timeout=None):
        calls.append(1)
        return _R("有关")
    monkeypatch.setattr(TM.urllib.request, "urlopen", _open)
    _fresh(tmp_path, monkeypatch)
    n = TM.prefetch([("SZ000001", ["a"], "AI/算力/科技"), ("SZ000002", ["b"], "AI/算力/科技"),
                     ("SZ000003", ["c"], "AI/算力/科技")], workers=3)
    assert n == 3 and len(calls) == 3
    n2 = TM.prefetch([("SZ000001", ["a"], "AI/算力/科技")], workers=3)
    assert n2 == 1 and len(calls) == 3                             # 预热后命中内存缓存


def test_默认超时下调与接线():
    src = open(os.path.join(_ROOT, "apps/main_line/theme_member_llm.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_MEMBER_LLM_TIMEOUT", "8")' in src       # 25s → 8s
    assert "member_cache" in src and "neg_cache" in src
    lg = open(os.path.join(_ROOT, "apps/main_line/leg_gate.py"), encoding="utf-8").read()
    assert "def prefetch(" in lg
    for f in ("jobs/bt_day_legs_switch.py", "jobs/bt_day_legs.py"):
        s = open(os.path.join(_ROOT, f), encoding="utf-8").read()
        assert "prefetch(" in s and "并发预热" in s, f

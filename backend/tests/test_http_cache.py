# -*- coding: utf-8 -*-
"""直连取数缓存单测（本地缓存 + 硬超时 + 负缓存）。"""
import json
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "apps", "main_line"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import http_cache as HC   # noqa: E402


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _fake_requests(calls, payload):
    m = types.ModuleType("requests")

    def _get(url, params=None, headers=None, verify=False, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        return _FakeResp(payload)
    m.get = _get
    return m


def test_硬超时封顶与正缓存(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(calls, {"data": {"items": [[1]]}}))
    monkeypatch.setattr(HC, "ON", True)
    monkeypatch.setattr(HC, "CACHE", str(tmp_path))
    monkeypatch.setattr(HC, "TIMEOUT", 6.0)
    HC._STATS.update({"get": 0, "hit": 0, "neg_hit": 0, "miss": 0, "err": 0, "saved_timeout": 0})
    r1 = HC.get_json("http://x/api", params={"a": 1}, asof="20260105", timeout=30)
    assert r1 == {"data": {"items": [[1]]}}
    assert calls and calls[0]["timeout"] == 6.0          # 调用方给 30s，被硬超时封到 6s
    r2 = HC.get_json("http://x/api", params={"a": 1}, asof="20260105", timeout=30)
    assert r2 == r1 and len(calls) == 1                  # 第二次命中缓存，没有再外呼
    assert HC._STATS["hit"] >= 1


def test_负缓存不再重打网络(tmp_path, monkeypatch):
    calls = []
    m = types.ModuleType("requests")

    def _boom(url, params=None, headers=None, verify=False, timeout=None):
        calls.append(1)
        raise RuntimeError("connection timeout")
    m.get = _boom
    monkeypatch.setitem(sys.modules, "requests", m)
    monkeypatch.setattr(HC, "ON", True)
    monkeypatch.setattr(HC, "CACHE", str(tmp_path))
    HC._STATS.update({"get": 0, "hit": 0, "neg_hit": 0, "miss": 0, "err": 0, "saved_timeout": 0})
    assert HC.get_json("http://x/bad", asof="20260105") is None
    assert len(calls) == 1
    assert HC.get_json("http://x/bad", asof="20260105") is None   # 负缓存命中
    assert len(calls) == 1                                        # 没有第二次外呼
    assert HC._STATS["neg_hit"] >= 1
    assert HC._STATS["saved_timeout"] >= 1                        # 记了"省下的超时"


def test_开关关时不写缓存(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(calls, {"ok": 1}))
    monkeypatch.setattr(HC, "ON", False)
    monkeypatch.setattr(HC, "CACHE", str(tmp_path))
    HC.get_json("http://x/y", asof="20260105")
    assert not list(tmp_path.glob("*.json"))


def test_接线存在():
    src = open(os.path.join(_ROOT, "apps/main_line/position_class.py"), encoding="utf-8").read()
    assert "from http_cache import get_json" in src and src.count("_gj") >= 2
    drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_HTTP_CACHE", "1")' in drv

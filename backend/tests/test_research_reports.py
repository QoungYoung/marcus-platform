# -*- coding: utf-8 -*-
"""研报抓取器（research_reports）单测 —— 2026-09-12。

重点钉死两条实测约束：① 服务抖动要重试；② **取数失败 ≠ 当天没有研报**（status 区分 ok/empty/failed）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import research_reports as RR  # noqa: E402


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._p = payload or {}
        self.text = text
    def json(self):
        return self._p


def test_parse_items_maps_fields():
    items = [["20260105", "国信证券_机械设备行业周报_20260105.pdf", "https://pdf.dfcfw.com/x.pdf",
              "行业研报", "张三", "机械设备", "000001.SZ", "国信证券"]]
    rows = RR.parse_items(items)
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_date"] == "20260105" and r["org"] == "国信证券" and r["report_type"] == "行业研报"
    assert r["title"].startswith("国信证券_")


def test_parse_items_tolerates_short_rows():
    assert RR.parse_items([["20260105"], None, "junk"]) == []


def test_fetch_day_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}
    def fake_get(url, params=None, headers=None, verify=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(504, {})
        return _Resp(200, {"code": 0, "data": {"items": [["20260105", "t", "u"]]}})
    import types
    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setattr(RR.time, "sleep", lambda *_: None)
    monkeypatch.setenv("PROMAX_API_KEY", "test-key")
    res = RR.fetch_day("20260105", attempts=3)
    assert res["status"] == "ok" and res["attempts"] == 3 and len(res["items"]) == 1


def test_fetch_day_marks_failed_not_empty(monkeypatch):
    """关键：连续失败必须标 failed（**不能**当成"当天没有研报"）。"""
    def fake_get(*a, **k):
        return _Resp(502, {})
    import types
    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setattr(RR.time, "sleep", lambda *_: None)
    monkeypatch.setenv("PROMAX_API_KEY", "test-key")
    res = RR.fetch_day("20260106", attempts=2)
    assert res["status"] == "failed" and res["items"] == [] and res["error"]


def test_fetch_day_empty_is_distinct(monkeypatch):
    def fake_get(*a, **k):
        return _Resp(200, {"code": 0, "data": {"items": []}})
    import types
    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setenv("PROMAX_API_KEY", "test-key")
    res = RR.fetch_day("20260911", attempts=1)
    assert res["status"] == "empty"


def test_key_read_from_env(monkeypatch):
    monkeypatch.delenv("PROMAX_API_KEY", raising=False)
    with pytest.raises(EnvironmentError):
        RR._key()

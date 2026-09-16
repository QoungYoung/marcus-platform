# -*- coding: utf-8 -*-
"""单测：日线底座自愈 `mkt_bars.ensure_fresh`（2026-09-15 用户指示：库里没新数据就直接调 relay 落库）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib  # noqa: E402

MB = importlib.import_module("app.services.mkt_bars")


def test_autosync_off_skips(monkeypatch):
    monkeypatch.setenv("WOLF_MKT_BARS_AUTOSYNC", "0")
    r = MB.ensure_fresh(today8="20260915", now_hm=1000)
    assert r.get("skipped") == "autosync_off"


def test_backfills_only_missing_closed_days(monkeypatch):
    monkeypatch.delenv("WOLF_MKT_BARS_AUTOSYNC", raising=False)
    monkeypatch.setattr(MB, "coverage", lambda: {"d1": "20260911"})
    monkeypatch.setattr(MB, "trade_days", lambda a, b: ["20260911", "20260914", "20260915", "20260916"])
    seen = {}

    def fake_backfill(start8, end8, **kw):
        seen.update({"start8": start8, "end8": end8, "days": kw.get("days")})
        return {"ok": True, "rows": 5550}

    monkeypatch.setattr(MB, "backfill", fake_backfill)
    r = MB.ensure_fresh(today8="20260915", now_hm=1000)   # 盘中：不含当天
    assert r["missing"] == ["20260914"]          # 09-11 已有、09-15/16 未收盘 → 只补 09-14
    assert seen["days"] == ["20260914"] and r["rows"] == 5550


def test_no_missing_days_does_not_call_backfill(monkeypatch):
    monkeypatch.delenv("WOLF_MKT_BARS_AUTOSYNC", raising=False)
    monkeypatch.setattr(MB, "coverage", lambda: {"d1": "20260914"})
    monkeypatch.setattr(MB, "trade_days", lambda a, b: ["20260914"])
    monkeypatch.setattr(MB, "backfill", lambda *a, **k: pytest.fail("不该回填"))
    r = MB.ensure_fresh(today8="20260915", now_hm=1000)
    assert r["missing"] == [] and r["rows"] == 0


def test_errors_do_not_raise(monkeypatch):
    monkeypatch.delenv("WOLF_MKT_BARS_AUTOSYNC", raising=False)
    monkeypatch.setattr(MB, "coverage", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = MB.ensure_fresh(today8="20260915", now_hm=1000)
    assert r["ok"] is False and "db down" in r["error"]


def test_after_close_includes_today(monkeypatch):
    """收盘后（≥15:30）跑，应把"今天"也纳入目标日（供 16:30 刷新链当天落库）。"""
    monkeypatch.delenv("WOLF_MKT_BARS_AUTOSYNC", raising=False)
    monkeypatch.setattr(MB, "coverage", lambda: {"d1": "20260914"})
    monkeypatch.setattr(MB, "trade_days", lambda a, b: ["20260914", "20260915"])
    seen = {}
    monkeypatch.setattr(MB, "backfill", lambda s8, e8, **kw: (seen.update({"days": kw.get("days")}), {"ok": True, "rows": 5550})[1])
    r = MB.ensure_fresh(today8="20260915", now_hm=1630)
    assert r["missing"] == ["20260915"] and seen["days"] == ["20260915"]


def test_default_clock_path_does_not_crash(monkeypatch):
    """回归（2026-09-16 生产事故）：不传 now_hm 时必须走系统时钟。

    原来这里用 _dt.date.today() 再取 .hour → date 对象没有 hour/minute，
    只要调用方不显式传 now_hm 就整体抛 "datetime.date object has no attribute hour"。
    16:30 收盘刷新链（jobs/refresh_index_daily.py → ensure_fresh(quiet=False)）恰好不传 now_hm，
    所以「PG 日线底座自愈」从上线起每天都是 ok=False、一天都没真正补过。
    """
    monkeypatch.delenv("WOLF_MKT_BARS_AUTOSYNC", raising=False)
    monkeypatch.setattr(MB, "coverage", lambda: {"d1": "20260911"})
    monkeypatch.setattr(MB, "trade_days", lambda a, b: ["20260911", "20260914", "20260915", "20260916"])
    seen = {}
    monkeypatch.setattr(MB, "backfill",
                        lambda s8, e8, **kw: (seen.update({"days": kw.get("days")}), {"ok": True, "rows": 5550})[1])
    r = MB.ensure_fresh(today8="20260915")                 # 不传 now_hm → 走系统时钟
    assert "error" not in r and r.get("ok") is True, r     # 修复前：{"ok": False, "error": "...no attribute hour"}
    assert "20260914" in r["missing"] and seen["days"] == r["missing"]

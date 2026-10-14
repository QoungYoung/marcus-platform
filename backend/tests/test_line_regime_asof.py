# -*- coding: utf-8 -*-
"""§9.868：黄白线/缩量的 **as-of** 取数（回测专用）✓

为什么要有它 ✗：`wolf_index_breadth.huang_bai()` 原本只有实时取数（腾讯 qt / spot）✗，
`wolf_line_regime.series()` 走 relay + 全局缓存（含最新日期 ✗）⇒ 直接搬进回测会灌入**未来数据** ✗
（实测：同一天"实时口径"给 side=bai ✗，as-of 口径给 side=huang ✓，结论相反 ✗）。
修 ✓：新增 as-of 分支读 `data/index_daily_<code>.json`（东财日线，纯历史 ✓）并按 ≤ as-of 日过滤 ✓；
取不到 ⇒ 返回空/None ⇒ **fail-open 放行** ✓（绝不回落实时 ✗）。
数据文件是本机缓存（不入库 ✓）⇒ 文件缺失时本用例自动跳过 ✓。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

_HAVE = all(os.path.exists(os.path.join(ROOT, "data", "index_daily_%s.json" % c))
            for c in ("000001_SH", "000852_SH"))


@pytest.mark.skipif(not _HAVE, reason="本机缺 data/index_daily_*.json（jobs/fetch_index_daily_em.py ✓）")
def test_asof_pair_is_date_filtered(monkeypatch):
    import app.services.wolf_index_breadth as B
    monkeypatch.setenv("WOLF_HUANG_BAI_SRC", "asof")
    monkeypatch.setattr(B, "_asof_day8", lambda: "20260506")
    hb, ix, _d, _n = B._asof_pair()
    # 05-06 实测 ✓：中证1000 +2.298% ｜ 上证 +1.168% ⇒ 黄在上 ✓
    assert round(hb, 2) == 2.30 and round(ix, 2) == 1.17
    monkeypatch.setattr(B, "_asof_day8", lambda: "20260512")   # 该日黄在下 ✓（白线在上 ✗）
    hb2, ix2, _d2, _n2 = B._asof_pair()
    assert hb2 < ix2, "05-12 应为白线在上（中证1000 弱于上证）✓"


def test_asof_missing_files_returns_none(monkeypatch):
    """取不到 ⇒ None ✓（**绝不回落实时** ✗ —— 否则回测会吃到未来数据 ✓）。"""
    import app.services.wolf_index_breadth as B
    monkeypatch.setattr(B, "_asof_rows", lambda code: [])
    assert B._asof_pair() is None


@pytest.mark.skipif(not _HAVE, reason="本机缺 data/index_daily_*.json")
def test_line_regime_shrink_is_asof(monkeypatch):
    import wolf_line_regime as LR
    monkeypatch.setenv("WOLF_LINE_REGIME_ASOF", "1")
    import datetime as _dt
    real = _dt.datetime

    class _F(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 5, 12, 14, 0, 0)

    monkeypatch.setattr(_dt, "datetime", _F)
    rows = LR.series()
    assert rows and rows[-1][0] == "20260512", "必须按 as-of 日截断（不得给未来行 ✓）"

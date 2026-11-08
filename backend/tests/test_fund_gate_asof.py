# -*- coding: utf-8 -*-
"""资金门 as-of 单测（2026-09-19）。

背景（实测 bug）：`theme_fund_danger` → `theme_nets(theme, days=n+2)` → `_trade_days(None, n)`
  走的是 `SELECT DISTINCT trade_date FROM mkt_bars_daily ORDER BY trade_date DESC LIMIT n`
  **没有上界** ⇒ 回放里拿到的是 **PG 库里最近的 n 个交易日**。本机 PG 的 mkt_bars_daily 只有
  `20260105~20260914`（min=20260105）⇒ 1 月的回放日拿到的是 **2026-09 的资金流**（未来函数）。
  铁证：`data/_bt_jan9/20260105/theme_mf_daily.json` 里被追加了 7 个 `20260904~20260914` 的日期。

用户口径（2026-09-19）：「主题资金流历史不要用当日的，避免未来函数」
  ⇒ 日 T 盘前只用 **≤ T-1**（沙箱里 = `_seed.json` 的 `cut`）的资金流。
开关 `WOLF_FUND_ASOF`：库内默认关（生产逐位不变，PG 里本来就没有未来数据）、回测开。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wolf_theme_vol_fund as V  # noqa: E402


def _sb(tmp_path, cut="20251231", cache=None):
    (tmp_path / "_seed.json").write_text(json.dumps({"cut": cut}), encoding="utf-8")
    if cache is not None:
        (tmp_path / "theme_mf_daily.json").write_text(json.dumps(cache), encoding="utf-8")
    return str(tmp_path)


def test_asof_锚优先沙箱cut(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", _sb(tmp_path, cut="2025-12-31"))
    assert V._asof_anchor() == "20251231"


def test_asof_无沙箱回落PG(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))          # 没有 _seed.json
    monkeypatch.setattr(V, "_prev_trade_day", lambda d8: "20260104")
    assert V._asof_anchor() == "20260104"


def test_theme_nets_只用不晚于锚的日期(monkeypatch, tmp_path):
    """PG 给未来日期（9 月）+ 缓存里有 as-of 日期 ⇒ 必须用缓存那几天，9 月的一个都不能进。"""
    cache = {"20251225": {"T1": -1}, "20251226": {"T1": -2}, "20251229": {"T1": -3},
             "20251230": {"T1": -4}, "20251231": {"T1": -5},
             "20260911": {"T1": 999.0}, "20260914": {"T1": 999.0}}
    monkeypatch.setenv("DATA_DIR", _sb(tmp_path, cut="20251231", cache=cache))
    monkeypatch.setattr(V, "FUND_ASOF_ON", True)
    monkeypatch.setattr(V, "_trade_days", lambda as_of, n: ["20260911", "20260914"])   # PG：未来日期
    assert V.theme_nets("T1", days=5) == [-1.0, -2.0, -3.0, -4.0, -5.0]


def test_theme_nets_开关关闭时保持旧行为(monkeypatch, tmp_path):
    """WOLF_FUND_ASOF=0 ⇒ 与历史逐位一致（把 PG 返回的日期原样用掉，便于回归对照）。"""
    cache = {"20260911": {"T1": 111.0}, "20260914": {"T1": 222.0}}
    monkeypatch.setenv("DATA_DIR", _sb(tmp_path, cut="20251231", cache=cache))
    monkeypatch.setattr(V, "FUND_ASOF_ON", False)
    monkeypatch.setattr(V, "_trade_days", lambda as_of, n: ["20260911", "20260914"])
    assert V.theme_nets("T1", days=2) == [111.0, 222.0]


def test_资金门_按as_of序列判危险(monkeypatch, tmp_path):
    """端到端：锚=cut，近 5 日全负 ⇒ 危险；值取自 as-of 缓存（不是 9 月）。"""
    cache = {d: {"T1": v} for d, v in zip(
        ["20251225", "20251226", "20251229", "20251230", "20251231"], [-10.0, -20.0, -30.0, -40.0, -50.0])}
    cache["20260914"] = {"T1": 9999.0}
    monkeypatch.setenv("DATA_DIR", _sb(tmp_path, cut="20251231", cache=cache))
    monkeypatch.setattr(V, "FUND_ASOF_ON", True)
    monkeypatch.setattr(V, "_trade_days", lambda as_of, n: ["20260914"])
    import types
    fm = types.ModuleType("fusion_mainline")
    fm.THEME_CONCEPTS = {"T1": ["c1"]}
    monkeypatch.setitem(sys.modules, "fusion_mainline", fm)
    import importlib
    import wolf_context as WC
    importlib.reload(WC)
    danger, why = WC.theme_fund_danger("T1")
    assert danger is True and "连续 5 日为负" in why
    assert "9999" not in why          # 未来（9 月）数据绝不能出现在判定里


def test_资金门_as_of序列里有正数则放行(monkeypatch, tmp_path):
    cache = {d: {"T1": v} for d, v in zip(
        ["20251225", "20251226", "20251229", "20251230", "20251231"], [-10.0, -20.0, 5.0, -40.0, -50.0])}
    monkeypatch.setenv("DATA_DIR", _sb(tmp_path, cut="20251231", cache=cache))
    monkeypatch.setattr(V, "FUND_ASOF_ON", True)
    monkeypatch.setattr(V, "_trade_days", lambda as_of, n: [])
    import types
    fm = types.ModuleType("fusion_mainline")
    fm.THEME_CONCEPTS = {"T1": ["c1"]}
    monkeypatch.setitem(sys.modules, "fusion_mainline", fm)
    import importlib
    import wolf_context as WC
    importlib.reload(WC)
    danger, why = WC.theme_fund_danger("T1")
    assert danger is False and "资金正常" in why

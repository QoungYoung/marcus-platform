# -*- coding: utf-8 -*-
"""买入层参数对齐：254 触发容差（C1）的定向单测（2026-09-15）。

背景（`docs/wolf-buy-parameter-ledger.md` §2-C1）：254 低吸触发的容差一直是**自设**的 `×1.005`，
而狼大 2025-03-06 逐字原话是「就是**挂前一天的低点** 能买进去就做正T 买不进去证明涨了 不用动 主升浪的做法」
→ 语料值 = **0.0**（挂前低本身）。

本测试钉死：
  1. 默认（无 env）= 语料值 0.0；
  2. `WOLF_DIP_PREVLOW_TOL=0.005` 可回退历史行为；
  3. 判据本身按 tol 生效：**恰好等于前日低**在 tol=0 时触发、在 -0.3% 时不触发；
     略高于前日低 0.3% 的价位在 tol=0 时不触发、在 tol=0.005 时触发；
  4. 缓存键带 tol（改 env 后不会被 30s 缓存掩盖）；
  5. 选票侧 `trig_price` 与 `t_monitor` 同源。
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _fresh_tm(monkeypatch, tol):
    """按指定 env 重新加载 t_monitor（常量在 import 时求值）。"""
    if tol is None:
        monkeypatch.delenv("WOLF_DIP_PREVLOW_TOL", raising=False)
    else:
        monkeypatch.setenv("WOLF_DIP_PREVLOW_TOL", str(tol))
    import app.services.t_monitor as tm
    return importlib.reload(tm)


def _fake_bars(prev_low, today_low, prev_day="20260912", today=None):
    """构造 5min bars：前一日 60 根（low=prev_low）+ 今日 60 根（low=today_low）→ 共 120 根。

    注意 `_stock_dip_prev_low` 有 `len(bars) < 100: return False` 的前置（真实接口一次取 320 根）。
    """
    import datetime as _dt
    today = today or _dt.datetime.now().strftime("%Y%m%d")
    if today == prev_day:                      # 避免两段撞在同一天
        today = (_dt.datetime.strptime(prev_day, "%Y%m%d") + _dt.timedelta(days=1)).strftime("%Y%m%d")
    bars = []
    for i in range(60):
        hh, mm = 9 + (30 + i * 5) // 60, (30 + i * 5) % 60
        bars.append({"time": prev_day + "%02d%02d" % (hh, mm), "low": prev_low,
                     "high": prev_low * 1.01, "close": prev_low, "open": prev_low, "volume": 100})
    for i in range(60):
        hh, mm = 9 + (30 + i * 5) // 60, (30 + i * 5) % 60
        bars.append({"time": today + "%02d%02d" % (hh, mm), "low": today_low,
                     "high": today_low * 1.01, "close": today_low, "open": today_low, "volume": 100})
    return bars


def _run(tm, monkeypatch, prev_low, today_low, today=None):
    import app.services.t_data_sources as tds
    monkeypatch.setattr(tds, "fetch_minute_bars",
                        lambda *a, **k: _fake_bars(prev_low, today_low, today=today), raising=False)
    tm._prev_low_cache.clear()
    obj = tm.TMonitor.__new__(tm.TMonitor)          # 不跑 __init__（避免依赖 DB/网络）
    return obj._stock_dip_prev_low("SH600000")


def test_default_is_corpus_value(monkeypatch):
    """默认 = 语料值 0.0（不是历史的 0.005）。"""
    tm = _fresh_tm(monkeypatch, None)
    assert tm.DIP_PREVLOW_TOL == 0.0
    assert "挂前一天的低点" in tm.TMonitor._stock_dip_prev_low.__doc__


def test_env_can_restore_legacy(monkeypatch):
    tm = _fresh_tm(monkeypatch, 0.005)
    assert tm.DIP_PREVLOW_TOL == 0.005
    tm2 = _fresh_tm(monkeypatch, None)
    assert tm2.DIP_PREVLOW_TOL == 0.0


def test_touch_exact_prev_low_fires_at_zero_tol(monkeypatch):
    """tol=0：恰好触到前日低 → 触发；比前日低高 0.3% → 不触发。"""
    tm = _fresh_tm(monkeypatch, None)
    assert _run(tm, monkeypatch, 10.00, 10.00) is True
    assert _run(tm, monkeypatch, 10.00, 10.03) is False       # +0.3% 不触发（旧 ×1.005 会触发）
    assert _run(tm, monkeypatch, 10.00, 9.95) is True         # 跌破 → 触发


def test_legacy_tol_still_fires(monkeypatch):
    """tol=0.005（回退档）：+0.3% 也触发，+0.6% 不触发。"""
    tm = _fresh_tm(monkeypatch, 0.005)
    assert _run(tm, monkeypatch, 10.00, 10.03) is True
    assert _run(tm, monkeypatch, 10.00, 10.06) is False


def test_cache_key_includes_tol(monkeypatch):
    """缓存必须带 tol：否则同一 symbol 在 30s 内会用上一次容差的结果。"""
    tm = _fresh_tm(monkeypatch, None)
    assert _run(tm, monkeypatch, 10.00, 10.03) is False
    assert tm._prev_low_cache.get("tol") == 0.0
    # 换容差（模拟灰度切换）后立即重算，不被缓存挡住
    tm.DIP_PREVLOW_TOL = 0.005
    assert _run(tm, monkeypatch, 10.00, 10.03) is True


def test_pick_trig_price_same_source(monkeypatch):
    """选票侧 trig_price 与 t_monitor 同源（同一个 env）。"""
    sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
    import wolf_confirm_pick as W
    monkeypatch.setenv("WOLF_DIP_PREVLOW_TOL", "0.0")
    assert W._dip_tol() == 0.0
    monkeypatch.setenv("WOLF_DIP_PREVLOW_TOL", "0.005")
    assert W._dip_tol() == 0.005
    src = open(os.path.join(ROOT, "apps", "main_line", "wolf_confirm_pick.py"), encoding="utf-8").read()
    assert "WOLF_DIP_PREVLOW_TOL" in src and "1.0 + _dip_tol()" in src
    tm_src = open(os.path.join(ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "1.0 + DIP_PREVLOW_TOL" in tm_src


def test_shadow_records_would_miss(monkeypatch, tmp_path):
    """C1 影子：tol=0 不触发但历史 0.005 会触发 → 记录到 data/dip_tol_shadow_<date>.json（不改行为）。"""
    import json
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WOLF_DIP_PREVLOW_SHADOW", raising=False)
    tm = _fresh_tm(monkeypatch, None)
    tm.DATA_DIR = str(tmp_path)
    assert tm._dip_shadow_enabled() is True
    # +0.3%：tol=0 不触发、0.005 触发 → 应记录
    assert _run(tm, monkeypatch, 10.00, 10.03) is False
    files = [p for p in os.listdir(tmp_path) if p.startswith("dip_tol_shadow_")]
    assert files, "影子文件未生成"
    d = json.load(open(os.path.join(tmp_path, files[0]), encoding="utf-8"))
    assert d["items"]["SH600000"]["note"].startswith("tol=0 不成交")
    # 恰好触前低：tol=0 也触发 → 不应新增记录
    before = len(d["items"])
    assert _run(tm, monkeypatch, 10.00, 10.00) is True
    d2 = json.load(open(os.path.join(tmp_path, files[0]), encoding="utf-8"))
    assert len(d2["items"]) == before

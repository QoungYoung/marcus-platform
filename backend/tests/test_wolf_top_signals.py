# -*- coding: utf-8 -*-
"""G1 顶部判据（wolf_top_signals）单测 —— 2026-09-14。

钉住的口径（每条对应狼大原话，见模块 docstring）：
  S1 2026-01-12 放量转缩量 ∧ 收黑K ∧ 收盘跌破 5 日线；
  S2 2025-05-06 放量上影线（上影>=30%全幅代理）∧ 量 >= 2×10 日均量；
  S3 2025-05-15 银保（ETF 代理）长上影 ∧ 放量 ∧ 大盘缩量；
  组合：**任一条成立**即"顶部阶段"（≥2 条在 355 天里 0 次触发，会变成休眠规则 —— 实测）；
  动作强度：仅信号触发 → 减半；原代理（120 日分位）触发 → 完全止盈。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_top_signals as TS  # noqa: E402


def _bar(d, o, h, l, c, v):
    return {"trade_date": d, "open": o, "high": h, "low": l, "close": c, "vol": v}


def _flat(days=30, px=10.0, v=1000.0):
    return [_bar("2026%04d" % (100 + i), px, px, px, px, v) for i in range(days)]


def test_s1_vol_expand_then_shrink_black_below_ma5():
    bars = _flat(12, 10.0, 1000.0)
    bars[-2] = _bar("x", 10.0, 10.2, 9.8, 10.1, 2000.0)     # 前一日放量（>=1.2×5日均量）
    bars[-1] = _bar("y", 10.1, 10.1, 9.4, 9.5, 1200.0)      # 当日缩量 + 收黑K + 收盘 9.5 < MA5(≈10)
    assert TS.sig_s1(bars, len(bars) - 1) is True
    ok = _flat(12, 10.0, 1000.0)
    ok[-1] = _bar("y", 9.4, 9.6, 9.3, 9.5, 1000.0)          # 没有"放量转缩量" → False
    assert TS.sig_s1(ok, len(ok) - 1) is False


def test_s2_volume_double_and_upper_shadow():
    bars = _flat(15, 10.0, 1000.0)
    bars[-1] = _bar("y", 10.0, 11.0, 10.0, 10.1, 2500.0)    # 上影远大于 30% 全幅 ∧ 量 2.5×10日均量
    assert TS.sig_s2(bars, len(bars) - 1) is True
    bars2 = _flat(15, 10.0, 1000.0)
    bars2[-1] = _bar("y", 10.0, 11.0, 10.0, 10.1, 1200.0)   # 量能不够（他要求 2 倍 10 日均量）
    assert TS.sig_s2(bars2, len(bars2) - 1) is False


def test_s3_bank_broker_shadow_volume_index_shrink():
    idx = _flat(15, 3000.0, 100000.0)
    idx[-1] = _bar("y", 2990, 3010, 2980, 2985, 80000.0)    # 大盘缩量
    bank = _flat(15, 1.0, 1000.0)
    bank[-1] = _bar("y", 1.0, 1.05, 1.0, 1.005, 2000.0)     # 长上影 + 放量
    broker = _flat(15, 1.0, 1000.0)
    d = str(idx[-1]["trade_date"])
    for b in (bank, broker, idx):
        b[-1]["trade_date"] = d
    assert TS.sig_s3(idx, bank, broker, len(idx) - 1) is True
    idx2 = _flat(15, 3000.0, 100000.0)
    idx2[-1] = _bar("y", 2990, 3010, 2980, 3005, 120000.0)  # 大盘放量 → 不成立
    for b in (idx2,):
        b[-1]["trade_date"] = d
    assert TS.sig_s3(idx2, bank, broker, len(idx2) - 1) is False


def test_s3_unknown_when_no_etf_data():
    idx = _flat(15, 3000.0, 100000.0)
    assert TS.sig_s3(idx, [], [], len(idx) - 1) is None      # 取不到 → unknown，不猜


def test_relay_loaded_by_file_path_not_sys_path():
    """relay 必须按**绝对文件路径**加载：sys.path 里 /app/app 排在 /app 前面时，
    import core.tushare_relay 会命中 /app/app/core（backend/app/core，命名空间包）→ ModuleNotFoundError。"""
    import inspect
    src = inspect.getsource(TS._relay)
    assert "spec_from_file_location" in src and "tushare_relay.py" in src
    assert 'from core.tushare_relay import' not in inspect.getsource(TS)


def test_top_stage_default_is_any_signal(monkeypatch):
    monkeypatch.delenv("WOLF_TOP_MIN_SIGNALS", raising=False)
    monkeypatch.setattr(TS, "top_signals", lambda d=None: {"S1": True, "S2": False, "S3": False,
                                                           "n": 1, "n_known": 3, "detail": "", "as_of": "x"})
    st = TS.top_stage()
    assert st["top"] is True and st["need"] == 1
    monkeypatch.setattr(TS, "top_signals", lambda d=None: {"S1": False, "S2": False, "S3": None,
                                                           "n": 0, "n_known": 2, "detail": "", "as_of": "x"})
    assert TS.top_stage()["top"] is False


def test_enabled_switch(monkeypatch):
    monkeypatch.setenv("WOLF_TOP_SIGNALS", "0")
    assert TS.enabled() is False
    monkeypatch.delenv("WOLF_TOP_SIGNALS", raising=False)
    assert TS.enabled() is True


def test_market_top_union_and_action_intensity(monkeypatch):
    """market_top 做宽=并集；仅信号触发 → reduce_ratio 0.5（他"减仓避一下"）。"""
    from app.services import wolf_boll_levels as BL
    monkeypatch.setattr(BL, "market_top", lambda force=False: {
        "pos": 0.2, "thr": 0.8, "top": True, "source": "signals", "win": 120,
        "signals": {"n": 1, "detail": "S1=True S2=False S3=False"}})
    monkeypatch.setattr(BL, "levels", lambda sym: {"mid": 10.0, "upper": 11.0, "prev_vol": 1000.0})
    pf = {"positions": [{"symbol": "SH600000", "avg_cost": 9.0, "volume": 1000}]}
    q = {"SH600000": {"current": 9.5, "vol": 1500.0}}
    sells = BL.mid_break_sells(pf, quotes=q)
    assert len(sells) == 1 and sells[0]["reduce_ratio"] == 0.5
    monkeypatch.setattr(BL, "market_top", lambda force=False: {
        "pos": 0.9, "thr": 0.8, "top": True, "source": "legacy", "win": 120, "signals": {"n": 0}})
    sells2 = BL.mid_break_sells(pf, quotes=q)
    assert sells2[0]["reduce_ratio"] == 1.0                  # 原代理 → 他的"完全止盈"

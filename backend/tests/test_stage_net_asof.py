# -*- coding: utf-8 -*-
"""个股资金流 as-of 接线（2026-09-22 用户："停掉 T6 立刻改完再重起"）。

背景：`stock_confirm_judge` 原先写死 `confirm_chain(ser, None, v)` ⇒ S1 的「抛压减弱(net_stop)」
从未参与判级，而它驱动的正是 253/254 低吸腿候选池；`bt_agent_tools` 对 AI 也恒返回 n/a。
数据实测可拉（tushare `moneyflow` 按票）⇒ 预拉到 `data/_bt_fund/_ref/moneyflow/<6位码>.json`。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

CACHE = REPO_ROOT / "data" / "_bt_fund" / "_ref" / "moneyflow"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WOLF_STAGE_NET_ASOF", "WOLF_AGENT_MONEYFLOW", "WOLF_FUND_ROOT"):
        monkeypatch.delenv(k, raising=False)


def _has_cache():
    return CACHE.is_dir() and any(CACHE.glob("*.json"))


def test_net_series_严格_as_of():
    if not _has_cache():
        pytest.skip("资金流缓存尚未预拉")
    import bt_fund_asof as F
    s = F.net_series("SZ000001", upto="20260408")
    assert s is not None and len(s) >= 15
    assert str(s.index[-1])[:10] <= "2026-04-08", "绝不能晚于 as-of 日"
    assert list(s.index) == sorted(s.index), "必须按日期升序（否则 net_stop 会算反）"
    s2 = F.net_series("SZ000001", upto="20250101")
    assert s2 is not None and str(s2.index[-1])[:10] <= "2025-01-01"
    assert F.net_series("SH999999", upto="20260408") is None, "无缓存 ⇒ None（fail-open）"


def test_net_为空时_confirm_chain行为不变(monkeypatch):
    """net=None（开关关 / 无缓存）⇒ net_stop 仍为 None、stage 与修复前一致。"""
    import pandas as pd
    import confirm_chain as CC
    ser = pd.Series([10.0 + (i % 3) * 0.01 for i in range(120)])
    vol = pd.Series([1e6] * 120)
    a = CC.confirm_chain(ser, None, vol)
    assert a["signals"].get("net_stop") is None


def test_net_到位时_net_stop参与判定():
    import pandas as pd
    import confirm_chain as CC
    ser = pd.Series([10.0 + (i % 3) * 0.01 for i in range(120)])
    vol = pd.Series([1e6] * 120)
    net = pd.Series([-1e7] * 10 + [0.0] * 5 + [1e7] * 5)   # 近 5 日均值(1e7) > 前 5 日均值(0) ⇒ 抛压减弱
    b = CC.confirm_chain(ser, net, vol)
    assert b["signals"].get("net_stop") is True


def test_judge_传net且有开关():
    src = (REPO_ROOT / "apps/main_line/stock_confirm_judge.py").read_text(encoding="utf-8")
    assert 'os.getenv("WOLF_STAGE_NET_ASOF", "0")' in src, "库内默认 0（生产逐位不变）"
    assert "_net = _net_for(ts_code" in src and "confirm_chain(ser, _net, v)" in src, "必须真的传进去"


def test_agent工具给AI真数据(monkeypatch):
    import bt_agent_tools as T
    assert T.moneyflow_on() is False, "库内默认 0"
    monkeypatch.setenv("WOLF_AGENT_MONEYFLOW", "1")
    assert T.moneyflow_on() is True
    src = (REPO_ROOT / "jobs/bt_agent_tools.py").read_text(encoding="utf-8")
    assert "get_stock_moneyflow" in src and "moneyflow_on()" in src
    assert "if t != \"get_stock_moneyflow\"" in src, "开时必须从 n/a 名单里摘掉"


def test_缺缓存时_按需补拉与负缓存(monkeypatch):
    """`WOLF_MF_FETCH_ON_MISS`：miss 当场补拉一次并写回；失败进负缓存不再重试。"""
    import bt_fund_asof as F
    calls = {"n": 0}

    def _fake_fetch(codes, tag=None, quiet=False, **kw):
        calls["n"] += 1
        return 1 if codes and str(codes[0]).endswith("600519") else 0

    monkeypatch.setattr(F, "fetch_moneyflow_by_code", _fake_fetch)
    monkeypatch.setattr(F, "_MISS", set())
    monkeypatch.setattr(F, "_FETCHED", {"n": 0})
    monkeypatch.setattr(F, "load_moneyflow", lambda code, tag=None: None)
    monkeypatch.delenv("WOLF_MF_FETCH_ON_MISS", raising=False)
    assert F.net_series("SH600519", "20260408") is None and calls["n"] == 0, "默认关：不发请求"
    monkeypatch.setenv("WOLF_MF_FETCH_ON_MISS", "1")
    assert F.net_series("SH600519", "20260408") is None and calls["n"] == 1, "开了：补拉一次（本桩返回假数据）"
    n0 = calls["n"]
    F.net_series("SH600519", "20260408")
    assert calls["n"] == n0, "失败进负缓存，不重试"
    monkeypatch.setenv("WOLF_MF_FETCH_MAX", "0")
    monkeypatch.setattr(F, "_MISS", set())
    F.net_series("SH601318", "20260408")
    assert calls["n"] == n0, "每进程上限为 0 ⇒ 一个都不拉"

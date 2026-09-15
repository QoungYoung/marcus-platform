# -*- coding: utf-8 -*-
"""单测：回测撮合的费用口径（2026-09-15 参数化，用户拍板按 0.1292%/往返）。

要钉死的行为：
  1. 默认口径 = 现行 A 股费率拆到单边（买 0.000396 / 卖 0.000896）→ **往返 0.1292%**；
  2. `BT_FEE_PROFILE=legacy` 能回到旧行为（买 0.0005 / 卖 0.0015 = 往返 0.2%，含 0.1% 印花税）；
  3. env 可分别覆盖单边费率；显式入参优先级最高；
  4. 与评估链口径一致：`jobs/eval_aligned_package.FEE_ROUNDTRIP == 0.1292`（防止两套口径分叉）。
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "backend"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BP = importlib.import_module("app.core.trading.backtest_paper")


def test_default_profile_is_current_a_share_rates(monkeypatch):
    for k in ("BT_FEE_PROFILE", "BT_COMMISSION_BUY", "BT_COMMISSION_SELL"):
        monkeypatch.delenv(k, raising=False)
    b, s, prof = BP.resolve_commission()
    assert abs(b - 0.000396) < 1e-12 and abs(s - 0.000896) < 1e-12
    assert abs((b + s) * 100 - 0.1292) < 1e-9
    assert prof == BP.FEE_PROFILE_DEFAULT
    assert BP.roundtrip_fee_pct() == 0.1292


def test_legacy_profile_restores_old_rates(monkeypatch):
    monkeypatch.setenv("BT_FEE_PROFILE", "legacy")
    b, s, _ = BP.resolve_commission()
    assert (b, s) == (BP.COMMISSION_BUY_LEGACY, BP.COMMISSION_SELL_LEGACY)
    assert BP.roundtrip_fee_pct() == 0.2


def test_env_and_explicit_override(monkeypatch):
    monkeypatch.setenv("BT_FEE_PROFILE", BP.FEE_PROFILE_DEFAULT)
    monkeypatch.setenv("BT_COMMISSION_BUY", "0.001")
    monkeypatch.setenv("BT_COMMISSION_SELL", "0.002")
    assert BP.resolve_commission()[:2] == (0.001, 0.002)
    assert BP.resolve_commission(buy=0.0, sell=0.0)[:2] == (0.0, 0.0)      # 显式入参最高优先
    monkeypatch.setenv("BT_FEE_PROFILE", "legacy")
    assert BP.resolve_commission(buy=0.0001)[:2] == (0.0001, 0.0015)       # 单边覆盖也生效


def test_matches_eval_chain_fee_constant():
    """评估链 `FEE_ROUNDTRIP=0.1292`（总账 §10）必须与撮合口径一致，防止又出现两套数字。"""
    src = open(os.path.join(ROOT, "jobs", "eval_aligned_package.py"), encoding="utf-8").read()
    assert "FEE_ROUNDTRIP = 0.1292" in src
    assert BP.roundtrip_fee_pct() == 0.1292

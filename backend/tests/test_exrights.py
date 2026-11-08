# -*- coding: utf-8 -*-
"""除权复权单测（用户 2026-09-22 拍板"修 3"）。

实测依据：T5/drabt5 全程 31 个闭环轮次里只有 1 轮持仓期遇除权，但它是账面最大单笔亏损
（SH603061 2026-04-14 买 315.26/323.30 → 04-20 卖 239.11，−5,960）：04-16 `pre_close`
从 333.66 被改成 229.85（10 送/转 ≈4.5 股）⇒ 固定股数记账凭空记出 −31%。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_exrights as X  # noqa: E402

BARS = str(REPO_ROOT / "data" / "_bt_full" / "bars.sqlite")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WOLF_EXRIGHTS_ADJUST", raising=False)
    monkeypatch.setenv("BT_BARS_DB", BARS)


def test_库内默认关():
    assert X.enabled() is False
    r = X.apply_day("drabt5", "20260416")
    assert r["adjusted"] == [] and r["on"] is False, "关着 ⇒ 一行都不动（生产零影响）"


def test_除权因子_真实数据():
    """SH603061 2026-04-16：昨收 333.66 / pre_close 229.85 ⇒ f≈1.4516。"""
    fac = X.factor("SH603061", "20260416")
    assert fac and 1.44 < fac["f"] < 1.46
    assert abs(fac["prev_close"] - 333.66) < 0.01 and abs(fac["pre_close"] - 229.85) < 0.01
    assert X.factor("SH603061", "20260415") is None, "非除权日 ⇒ 不动"
    assert X.factor("SH603986", "20260416") is None, "兆易创新 0416 无除权"


def test_方案_一手仓遇送转():
    """100 股 × 1.4516 = 145 股 ⇒ 按手取整 100 股 + 零股 45.16 股折现；成本摊薄。"""
    p = X.plan_position(100, 319.28, 1.4516, 229.85)
    assert p["ok"] and p["lot"] == 100
    assert abs(p["credit"] - (145.16 - 100) * 229.85) < 1.0
    # 成本 31,928 − 折现 ≈ 21,547 ⇒ 每股 ≈215.5（不再是 319.28 ⇒ 卖出不再凭空亏 31%）
    assert abs(p["new_avg"] - (319.28 * 100 - p["credit"]) / 100.0) < 0.01
    assert 210 < p["new_avg"] < 220


def test_方案_两手仓遇送转():
    """200 股 ⇒ 290 股 ⇒ 取整 200 + 零股 90 股折现。"""
    p = X.plan_position(200, 300.0, 1.45, 200.0)
    assert p["ok"] and p["lot"] == 200
    assert abs(p["credit"] - 90 * 200.0) < 0.01


def test_方案_边界():
    assert X.plan_position(0, 300.0, 1.45, 200.0)["ok"] is False
    assert X.plan_position(100, 0, 1.45, 200.0)["ok"] is False
    assert X.plan_position(50, 300.0, 1.45, 200.0)["ok"] is False, "调整后不足一手 ⇒ 不调"
    assert X.plan_position(100, 300.0, 1.0, 300.0)["ok"] is False, "无事件 ⇒ 不调"


def test_行重述_股数与成本守恒():
    lots = [[11, 315.26, 100], [12, 323.30, 100]]
    k = 200.0 / 200.0
    rows = X.restate_rows(lots, k, 0.0, 200 * 319.28)
    assert sum(r[2] for r in rows) == 200
    cost = sum(r[1] * r[2] for r in rows)
    assert abs(cost - 200 * 319.28) < 1.0, "总成本必须守恒"
    # 带折现：总成本按比例下调
    rows2 = X.restate_rows(lots, 1.0, 1000.0, 200 * 319.28)
    assert abs(sum(r[1] * r[2] for r in rows2) - (200 * 319.28 - 1000.0)) < 1.0


def test_行重述_缩股():
    lots = [[11, 300.0, 200]]
    rows = X.restate_rows(lots, 1.45, 0.0, 200 * 300.0)
    assert sum(r[2] for r in rows) == 290
    assert abs(sum(r[1] * r[2] for r in rows) - 200 * 300.0) < 1.0, "股数变多、总成本不变"


def test_接线_盘前跑一次():
    src = (REPO_ROOT / "backend/app/services/t_monitor.py").read_text(encoding="utf-8")
    body = src[src.index("def _daily_maintain"):]
    assert "wolf_exrights" in body and "apply_day" in body, "必须在每日维护（首次轮询前）里跑"

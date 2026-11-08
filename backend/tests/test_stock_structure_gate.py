# -*- coding: utf-8 -*-
"""个股结构门单测（2026-09-19 用户拍板 A；蓝图:149「下跌不做」）。

背景：jan11 0128–0130 有 4 笔买入，其中两笔在个股下跌结构里接刀——
  · SH603019 0128 11:05 100@91.229（MA5 95.56/MA10 94.48/MA20 91.83，同时破三条）→ 0130 卖 89.88，−148；
  · SZ002579 0128 13:40 800@12.318（MA5 12.63/MA10 12.53/MA20 12.37，同时破三条，且是给亏损仓加仓）
    → 0129 尾盘卖 3300@11.89，−1,550。
正T买入腿（wolf_zheng_t_buy）由形态触发，不看个股结构 ⇒ 本门补上「现价 < MA20 且 MA10 < MA20 ⇒ 不买」。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_stock_structure as S  # noqa: E402


def _bars(closes):
    return [{"date": "2026%04d" % (100 + i), "close": float(c), "high": float(c), "low": float(c), "vol": 1}
            for i, c in enumerate(closes)]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(S, "ON", True)
    monkeypatch.setattr(S, "MIN_BARS", 20)


def test_下跌结构拦买腿():
    down = _bars([100 - i * 0.8 for i in range(25)])
    ok, why = S.verdict("SH603019", down[-1]["close"], down)
    assert ok is False and "下跌结构" in why and "下跌不做" in why


def test_上升结构放行():
    up = _bars([100 + i * 0.8 for i in range(25)])
    assert S.verdict("SH600183", up[-1]["close"] + 0.2, up)[0] is True


def test_只破MA5的回调放行():
    """002518 式回调（仍在 MA10/MA20 上方）不该被拦。"""
    up = _bars([100 + i * 0.8 for i in range(25)])
    assert S.verdict("SZ002518", up[-1]["close"] - 0.5, up)[0] is True


def test_横盘放行():
    flat = _bars([50 + (0.1 if i % 2 else -0.1) for i in range(25)])
    assert S.verdict("SH603068", 50.0, flat)[0] is True


def test_数据不足failopen():
    assert S.verdict("X", 10.0, _bars([10, 9, 8]))[0] is True
    assert S.verdict("X", 0, _bars([100 - i for i in range(25)]))[0] is True
    assert S.verdict("X", 10.0, None)[0] is True


def test_开关关闭全放行(monkeypatch):
    monkeypatch.setattr(S, "ON", False)
    down = _bars([100 - i * 0.8 for i in range(25)])
    assert S.verdict("SH603019", down[-1]["close"], down)[0] is True


def test_is_downtrend_纯结构判定_不受开关影响(monkeypatch):
    """is_downtrend 供卖出侧"趋势转弱"豁免复用，必须**不受 WOLF_STOCK_TREND_GATE 影响**。"""
    monkeypatch.setattr(S, "ON", False)
    down = _bars([100 - i * 0.8 for i in range(25)])
    up = _bars([100 + i * 0.8 for i in range(25)])
    assert S.is_downtrend(down[-1]["close"], down) is True
    assert S.is_downtrend(up[-1]["close"], up) is False
    assert S.is_downtrend(10.0, None) is False
    assert S.is_downtrend(10.0, _bars([10, 9, 8])) is False


def test_接线_monitor买腿路径已接上():
    src = (REPO_ROOT / "backend" / "app" / "services" / "t_monitor.py").read_text(encoding="utf-8")
    assert "wolf_stock_structure" in src and "个股结构门" in src, "买腿路径没接个股结构门"
    assert "struct_weak=_sw" in src, "卖出侧没接『趋势转弱 ⇒ 穿底仓』"
# -*- coding: utf-8 -*-
"""「不追高」确定性闸单测（2026-09-19 用户拍板 A，落地自台账 §29.1）。

语料：2025-04-03「跌下来可以找买点，但是冲上去一定不能追」；2025-10-09「任何时候不追高追涨」；
2025-06-25「没量就别追」；2025-04-15 条件6「如果当日开盘高开快速拉升，或者低开快速拉升想追进去的，
在下午2.00-2.30这个时间段进行回补」。台账 §29.1 的结论：**不是绝不追，而是不在开盘追**。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_no_chase as N  # noqa: E402


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(N, "ON", True)
    monkeypatch.setattr(N, "RISE_PCT", 2.0)
    monkeypatch.setattr(N, "QUANTILE", 85.0)      # 2026-09-19 用户拍板 C：且语义 + 门槛 85
    monkeypatch.setattr(N, "PREMIUM_PCT", 0.0)
    monkeypatch.setattr(N, "PM_WINDOW", "1400-1430")


def test_回踩低吸不受影响():
    """现价 ≤ 建议买价（真回踩）⇒ 放行。"""
    ok, why = N.verdict(64.0, 64.575, rise_pct=3.0, quantile=95.0, hhmm="0955")
    assert ok is True and "回踩低吸" in why


def test_冲上去追高_开盘时段拦():
    """现价高于买价 + 拉升≥2% **且** 分位≥85 + 不在 14:00–14:30 ⇒ 拦。"""
    ok, why = N.verdict(64.64, 64.575, rise_pct=2.6, quantile=88.0, hhmm="0955")
    assert ok is False and "不追高" in why and "一定不能追" in why


def test_且语义_只满足一条就放行():
    """2026-09-19 用户拍板 C：判据是「拉升≥2% **且** 分位≥85」——只满足一条不算追高。

    实测 0106 六笔对照：603893(3.15%/99.2)、603383(2.99%/98.2)、603019(4.11%/94.5) 仍拦；
    002364(2.70%/80.7)、600183(2.23%/69.7) 放行；601138(2.77%/84.06) 恰在 85 下方 0.94pt ⇒ 放行（阈值待校）。
    """
    assert N.verdict(64.64, 64.575, rise_pct=2.70, quantile=80.7, hhmm="0955")[0] is True   # 分位不够
    assert N.verdict(64.64, 64.575, rise_pct=1.50, quantile=95.0, hhmm="0955")[0] is True   # 拉升不够
    assert N.verdict(64.64, 64.575, rise_pct=2.77, quantile=84.06, hhmm="0955")[0] is True  # 601138 临界（85 门槛）
    assert N.verdict(64.64, 64.575, rise_pct=2.99, quantile=98.2, hhmm="0955")[0] is False  # 两条都够 ⇒ 拦


def test_冲高但在他给的追高窗口内_放行():
    """2025-04-15 条件6：想追就延后到 14:00–14:30 ⇒ 同一情形在窗内放行。"""
    ok, why = N.verdict(64.64, 64.575, rise_pct=2.6, quantile=88.0, hhmm="1405")
    assert ok is True and "追高窗口" in why


def test_高于买价但没拉升_放行():
    ok, why = N.verdict(64.64, 64.575, rise_pct=0.4, quantile=40.0, hhmm="0955")
    assert ok is True and ("未同时满足" in why or "未拉升" in why)


def test_拉升数据缺失_failopen():
    ok, _ = N.verdict(64.64, 64.575, rise_pct=None, quantile=None, hhmm="0955")
    assert ok is True
    ok2, why2 = N.verdict(0, 0, hhmm="0955")
    assert ok2 is True and "缺现价" in why2


def test_卖腿不适用():
    ok, why = N.verdict(64.64, 64.575, rise_pct=9.0, quantile=99.0, hhmm="0955", side="sell")
    assert ok is True and "卖腿" in why


def test_开关关闭全放行(monkeypatch):
    monkeypatch.setattr(N, "ON", False)
    ok, why = N.verdict(64.64, 64.575, rise_pct=9.0, quantile=99.0, hhmm="0955")
    assert ok is True and "关闭" in why


def test_阈值可调(monkeypatch):
    monkeypatch.setattr(N, "RISE_PCT", 5.0)
    monkeypatch.setattr(N, "QUANTILE", 99.0)
    ok, _ = N.verdict(64.64, 64.575, rise_pct=2.6, quantile=88.0, hhmm="0955")
    assert ok is True, "阈值调高后同一情形不再算追高"


def test_接线_快照写入与网关判定存在():
    """护栏：monitor 必须把拉升/分位写进快照，网关必须调用不追高闸（否则闸是空转）。"""
    mon = (REPO_ROOT / "backend" / "app" / "services" / "t_monitor.py").read_text(encoding="utf-8")
    gw = (REPO_ROOT / "backend" / "app" / "services" / "t_gateway.py").read_text(encoding="utf-8")
    assert "day_rise_pct" in mon and "day_quantile" in mon, "monitor 未把拉升/分位写进触发快照"
    assert "wolf_no_chase" in gw and "NO_CHASE" in gw, "网关未接不追高闸"
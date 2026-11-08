# -*- coding: utf-8 -*-
"""换手治理 ③「做T时段窗」的**作用面**（2026-09-19 用户拍板 A：按语料缩面）。

## 语料划的界（playbook:287 + 同篇适用范围声明）

「当日只做上午 9:45–10:00、下午 14:00–14:30 两个时间段…避免平稳时段来回 T」，且同篇写明
「这里说的买卖方法一般对应的**单日或者 3 日内**，如果是**大级别的买入和卖出是另外一个方法**」。
同一条语料的 A5 模块（wolf_trade_window）据此写明：**只有** low_buy/custom_prevlow 受时间窗约束；
custom_m5dump（253 急杀开小底仓）属**建仓/大级别**不受；卖腿/止损也不受。

## 缺陷

本闸 ③ 原先对**所有非保护性委托**（买+卖）一律套时段窗 ⇒ 实测把 custom_m5dump 建仓、
wolf_defensive_t_reduce 防御性减仓、wolf_fib_target_sell 目标止盈也拦了
（jan10 被 ③ 拦 298 条：sell 297 / buy 1；jan9 249 条、jan7 243 条，其中各有 12 条 custom_prevlow 买腿）。

## 修法

③ 只对**做T类白名单**生效（WOLF_TURNOVER_TW_KINDS，默认
low_buy/custom_prevlow/wolf_zheng_t_buy + custom_vwap_sell/custom_support_sell/high_sell/wolf_dao_t_sell）；
网关把腿型传进来（trigger.event_type 优先，退回 condition.trigger_kind）。腿型取不到时默认**仍拦**
（保守，维持旧行为），WOLF_TURNOVER_TW_UNKNOWN=allow 可放行。
①同价来回 / ②下去不补 / ④跨日冷却 与腿型无关，照旧全适用。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_turnover as T  # noqa: E402

OUT = datetime(2026, 1, 12, 13, 45)      # 窗外（做T时段是 09:45–10:00 / 14:00–14:30）
IN = datetime(2026, 1, 12, 9, 50)        # 窗内


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(T, "GUARD_ON", True)
    monkeypatch.setattr(T, "_recent", lambda *a, **kw: [])
    monkeypatch.setattr(T, "TW_UNKNOWN_ALLOW", False)


def _ok(kind, side="buy", now=OUT, reason=""):
    return T.check("SH600850", side, 32.5, "drabjan10", reason=reason, now=now, trigger_kind=kind)


def test_做T买腿_窗外被拦():
    ok, why = _ok("custom_prevlow")
    assert ok is False and "做T时段窗" in why
    ok2, _ = _ok("wolf_zheng_t_buy")
    assert ok2 is False


def test_做T卖腿_窗外被拦():
    for k in ("custom_vwap_sell", "custom_support_sell", "high_sell", "wolf_dao_t_sell"):
        ok, why = _ok(k, side="sell")
        assert ok is False and "做T时段窗" in why, k


def test_建仓腿_窗外放行():
    """253 急杀开小底仓 = 建仓/大级别（语料自己划的界）⇒ 不受时段窗。"""
    ok, why = _ok("custom_m5dump")
    assert ok is True, why


def test_减仓与止盈_窗外放行():
    for k in ("wolf_defensive_t_reduce", "wolf_fib_target_sell", "wolf_confirm_sell"):
        ok, why = _ok(k, side="sell")
        assert ok is True, (k, why)


def test_窗内_做T类照常放行():
    for k in ("custom_prevlow", "wolf_zheng_t_buy"):
        ok, why = _ok(k, now=IN)
        assert ok is True, (k, why)


def test_腿型未知_默认仍拦_可配放行(monkeypatch):
    ok, why = _ok("")
    assert ok is False and "做T时段窗" in why
    monkeypatch.setattr(T, "TW_UNKNOWN_ALLOW", True)
    ok2, why2 = _ok("")
    assert ok2 is True, why2


def test_保护性卖出仍豁免():
    ok, why = _ok("custom_vwap_sell", side="sell", reason="止损离场")
    assert ok is True, why
    ok2, _ = T.check("SH600850", "sell", 32.5, "drabjan10", reason="", is_stop_loss=True,
                     now=OUT, trigger_kind="custom_vwap_sell")
    assert ok2 is True


def test_白名单可配(monkeypatch):
    monkeypatch.setattr(T, "TW_KINDS", ["custom_m5dump"])
    assert T.applies_to("custom_m5dump") is True
    assert T.applies_to("custom_prevlow") is False
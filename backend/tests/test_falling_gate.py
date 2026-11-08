# -*- coding: utf-8 -*-
"""A+B+C 单测（2026-09-22 用户拍板；起因：环旭电子 601231 2026-03-03 买在 −10% 瀑布日的高位）。

· A `WOLF_TURNOVER_PROTECT_SELL_ONLY`：换手闸的保护性委托豁免**只对卖出**。
  原实现 `any(k in reason for k in ("止损","破位","风控"...))` 对买卖一视同仁，而 reason 是调用方传的
  自由文本（AI 决策理由）⇒ AI 写买入理由时的否定句「未跌破止损」会**整段跳过 ①②③④**。
· B `WOLF_FALLING_GATE`：现价 < 当日 VWAP ∧（自开盘逐根走低 ∨ 跌破当日开盘）⇒ 不在下跌中买。
· C `WOLF_WEAK_DEFER`：当日为跌 ∧ 现价 < VWAP ∧ 未到 14:45 ⇒ 延后到尾盘重评。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import intraday_crush as C                      # noqa: E402
from app.services import t_turnover as T        # noqa: E402

MINS = REPO_ROOT / "data" / "_bt_full" / "mins"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WOLF_FALLING_GATE", "WOLF_WEAK_DEFER", "WOLF_WEAK_DEFER_HM",
              "WOLF_TURNOVER_PROTECT_SELL_ONLY", "WOLF_TURNOVER_GUARD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WOLF_MINS_DIR", str(MINS))


# ── 时间过滤（用户点名的那条根因）──────────────────────────────
def test_upto_两种时间写法结果一致():
    """`_upto` 曾拿 `"09:35"` 比 `"0935"`（冒号 > 数字）⇒ 09:xx 取到 0 根 bar ⇒ 整条闸 fail-open。"""
    bars = C._bars("601231_SH", "20260303")
    assert bars, "缺分钟档 ⇒ 用例前提不成立"
    assert len(C._upto(bars, "0940")) == len(C._upto(bars, "09:40")) == 3
    assert len(C._upto(bars, "1000")) == len(C._upto(bars, "10:00")) == 7
    assert len(C._upto(bars, "0935")) == 2, "09:35 有 2 根（集合竞价 + 第一根 5min）"


def test_开盘头十分钟不再fail_open():
    f = C.feats("601231_SH", "20260303", "0940", min_bars=2)
    assert f and f["nbars"] == 3 and f["below_open"] is True and f["nb_down"] is True
    assert C.feats("601231_SH", "20260303", "0935", min_bars=2)["nbars"] == 2
    assert C.feats("601231_SH", "20260303", "0935") is None, "默认 min_bars=3 时仍按原口径（C2′/C2″ 行为不变）"


# ── B：不在下跌中买 ───────────────────────────────────────────
def test_B_环旭电子跌停日_0935与0940都要拦(monkeypatch):
    monkeypatch.setenv("WOLF_FALLING_GATE", "1")
    for hm in ("0935", "0940"):
        ok, why = C.timing_verdict("SH601231", "20260303", hm, pre_close=48.42)
        assert ok is False, "%s 必须拦（用户点名：9.35 不能被放行）" % hm
        assert "不在下跌中买" in why and "缓跌不买" in why


def test_B_正常上涨日的低吸_放行(monkeypatch):
    monkeypatch.setenv("WOLF_FALLING_GATE", "1")
    ok, why = C.timing_verdict("SH603893", "20260105", "0940", pre_close=175.0)
    assert ok is True, why


def test_B_纯函数三分支(monkeypatch):
    monkeypatch.setenv("WOLF_FALLING_GATE", "1")
    base = {"px": 10.0, "vwap": 10.5, "open": 10.8, "nb_down": False, "last3_down": False, "below_open": True}
    assert C.falling(base)[0] is True, "跌破开盘即算下跌中"
    assert C.falling(dict(base, below_open=False, nb_down=True))[0] is True, "逐根走低也算"
    assert C.falling(dict(base, below_open=False, nb_down=False))[0] is False, "均价下方但没走低/没破开盘 ⇒ 放行"
    assert C.falling(dict(base, px=11.0))[0] is False, "在均价上方 ⇒ 放行"
    assert C.falling({"px": 10.0, "vwap": None})[0] is False, "缺均价 ⇒ 放行(fail-open)"


def test_卖腿一律放行(monkeypatch):
    monkeypatch.setenv("WOLF_FALLING_GATE", "1")
    monkeypatch.setenv("WOLF_WEAK_DEFER", "1")
    assert C.timing_verdict("SH601231", "20260303", "0940", pre_close=48.42, side="sell")[0] is True


def test_两个开关都关时逐字放行():
    ok, why = C.timing_verdict("SH601231", "20260303", "0940", pre_close=48.42)
    assert ok is True and why == "", "库内默认关 ⇒ 生产零影响"


# ── C：弱势日延后到尾盘 ───────────────────────────────────────
def test_C_弱势日盘前拦_尾盘放行(monkeypatch):
    monkeypatch.setenv("WOLF_WEAK_DEFER", "1")
    monkeypatch.setenv("WOLF_WEAK_DEFER_HM", "1445")
    ok, why = C.timing_verdict("SH601231", "20260303", "0940", pre_close=48.42)
    assert ok is False and ("延后到尾盘" in why or "不在下跌中买" in why)
    # 该票当日一路跌到跌停，14:50 仍属"下跌中"（B 未开时 C 应放行）
    ok2, _ = C.timing_verdict("SH601231", "20260303", "1450", pre_close=48.42)
    assert ok2 is True, "过了 1445 ⇒ C 不再拦（B 关着）"


def test_C_强势股不延后(monkeypatch):
    monkeypatch.setenv("WOLF_WEAK_DEFER", "1")
    ok, _ = C.timing_verdict("SH603893", "20260105", "0940", pre_close=175.0)
    assert ok is True


# ── A：换手闸的保护性豁免只对卖出 ─────────────────────────────
AI_BUY_REASON = ("震荡市regime允许自动执行；现价47.39与建议买价47.39价差0%（无脱节）、未跌破止损、"
                 "09:40仅回踩至47.39仍高于日内低点47.00且分位26%非放量创新低恐慌，按默认动作执行。")


def test_A_买腿不再被理由文本旁路(monkeypatch):
    # ⚠️ 用 monkeypatch 改属性而不是 importlib.reload：reload 会把"带开关的模块"留在内存里，
    #    污染同一 session 里其它测试（我第一版就这么踩了 test_turnover_guard::test_default_off）。
    monkeypatch.setattr(T, "GUARD_ON", True)
    monkeypatch.setenv("WOLF_TURNOVER_PROTECT_SELL_ONLY", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    # 带冒号的窗口用时点：09:40 不在 09:45–10:00 ⇒ 应变"拦"
    import datetime as _dt
    now = _dt.datetime(2026, 3, 3, 9, 40)
    ok, why = T.check("SH601231", "buy", 47.39, "drabt5", reason=AI_BUY_REASON,
                      trigger_kind="wolf_zheng_t_buy", now=now)
    assert ok is False and "做T时段窗" in why, "买腿不能再靠『未跌破止损』整段豁免"
    # 同样的理由，卖腿仍应豁免（保护性卖出必须能走）
    ok2, _ = T.check("SH601231", "sell", 47.39, "drabt5", reason=AI_BUY_REASON,
                     trigger_kind="custom_support_sell", now=now)
    assert ok2 is True, "保护性卖出照旧放行"


def test_A_开关关时保持旧行为(monkeypatch):
    monkeypatch.setattr(T, "GUARD_ON", True)
    monkeypatch.setenv("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    import datetime as _dt
    ok, _ = T.check("SH601231", "buy", 47.39, "drabt5", reason=AI_BUY_REASON,
                    trigger_kind="wolf_zheng_t_buy", now=_dt.datetime(2026, 3, 3, 9, 40))
    assert ok is True, "开关关 ⇒ 与修复前逐位一致（生产零影响）"


# ── 接线 ─────────────────────────────────────────────────────
def test_接线_三条路径():
    mon = (REPO_ROOT / "backend/app/services/t_monitor.py").read_text(encoding="utf-8")
    gw = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
    assert "_defer_fall" in mon and "not _defer_fall" in mon, "wolf 腿路径要真的进判据链"
    assert "intraday_timing_blocked" in mon, "执行口兜底 helper"
    assert 'level": "BUY_TIMING"' in gw, "网关执行口兜底"
    assert "timing_verdict" in gw

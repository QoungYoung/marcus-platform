# -*- coding: utf-8 -*-
"""分钟 bar 时间戳格式鲁棒性单测（2026-09-17，P0 缺陷 1）。

## 缺陷（修复前）

`t_monitor` 里所有"筛当日 bar"的比较都是**字面前缀**匹配：
    `str(b["time"]).startswith(datetime.now().strftime("%Y-%m-%d"))`   # 带横线
而生产分钟源（`t_data_sources.fetch_minute_bars`，brze 中继）返回的是 **12 位 `YYYYMMDDHHMM`（无横线）**，
两者永不匹配 → 当日 bar 恒空。后果：
  · `_settle_tsell_pending`：撤销式观察期**永不结算**（t_triggers 里 13 条"撤销式观察"恒停 claimed；
    且每个标的每天第一笔 high_sell 被永久吞掉 = 观察期语义整体失效）；
  · `_build_minute_snapshot`：`minute.m1.low_today` 恒 0.0；
  · `stabilize_not_new_low_at`：`today_lows` 恒空 → 直接 `return True`（企稳**恒真，fail-open**）；
  · `_index_intraday_dd`：当日 bar 恒空 → 盘中回撤恒 0.0（这处旧写法是 `%Y%m%d`，
    对带横线的时间戳同样失配，即两个方向都会踩）。
修复：统一走 `_bar_date8()`（两侧都归一成 8 位日期，不依赖分隔符）。

**不要**用"把 shim 的时间戳改回带横线"来绕过——`_stock_dip_prev_low` 依赖
`str(time)[:8]` 取日期（12 位口径），改回去会让 254 买腿永不触发（回测侧注释已写明）。
"""
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M  # noqa: E402
import app.services.t_data_sources as TDS  # noqa: E402
import app.services.t_gateway as TG  # noqa: E402

NOW = datetime(2026, 9, 17, 10, 0, 0)
TODAY8 = "20260917"
DIGITS = "202609171035"           # 生产（brze 中继）：12 位无分隔符
DASHED = "2026-09-17 10:35:00"    # 回测 / 腾讯 mkline：带横线
YDAY_DIGITS = "202609161035"      # 前一交易日（12 位）
YDAY_DASHED = "2026-09-16 14:55:00"


class _PinnedDT(datetime):
    """把 t_monitor.datetime.now() 钉到 2026-09-17 10:00（与 jobs/bt_run_pinned.pin_clock 同思路）。"""

    @classmethod
    def now(cls, tz=None):
        return datetime(NOW.year, NOW.month, NOW.day, NOW.hour, NOW.minute, NOW.second)


# ────────────────────────── ① _bar_date8 归一 ──────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("202609171035", "20260917"),            # 生产 12 位
    ("20260917103500", "20260917"),          # 14 位
    ("20260917", "20260917"),
    ("2026-09-17 10:35:00", "20260917"),     # 回测/腾讯
    ("2026-09-17T10:35", "20260917"),
    ("2026-09-17", "20260917"),
    ("2026/9/7 09:35", "20260907"),          # 不补零也要能解析
    ("2026.09.17 10:35", "20260917"),
    ("1035", ""),                            # 只有 HHMM：不能瞎猜成日期
    ("", ""),
    (None, ""),
])
def test_bar_date8_normalizes_both_formats(raw, expect):
    assert M._bar_date8(raw) == expect


def test_old_prefix_filter_was_empty_for_production_format():
    """回归锚点：旧的字面前缀比较对生产 12 位时间戳恒空（这就是缺陷本身）。"""
    bars = [{"time": DIGITS}, {"time": "202609171040"}]
    assert [b for b in bars if str(b["time"]).startswith(NOW.strftime("%Y-%m-%d"))] == []
    assert len([b for b in bars if M._bar_date8(b["time"]) == TODAY8]) == 2


# ────────────────────────── ② 撤销式T出结算 ──────────────────────────

def _settle(bars, pending_ts_offset=700, hi=10.0, base=100.0):
    """跑一次 `_settle_tsell_pending`，返回 (update_calls, gw_mock, pending_after)。"""
    M._TSELL_PENDING.clear()
    M._TSELL_PENDING["SH600000"] = {
        "hi": hi, "base": base, "volume": 100, "trig_id": 7,
        "ts": time.time() - pending_ts_offset, "account_id": "stock", "last_price": 9.5,
    }
    updates = []
    with mock.patch.object(M, "datetime", _PinnedDT), \
         mock.patch.object(TDS, "fetch_minute_bars", return_value=bars), \
         mock.patch.object(TDS, "fetch_tencent_quote", return_value={"SH600000": {"current": 9.5}}), \
         mock.patch.object(M.t_db, "update_trigger_status",
                           side_effect=lambda *a, **k: updates.append((a, k)) or True), \
         mock.patch.object(TG, "gateway_execute",
                           return_value={"status": "success", "reason": ""}) as gw:
        M.TMonitor._settle_tsell_pending(object())   # self 未被使用
    return updates, gw, dict(M._TSELL_PENDING)


@pytest.fixture(autouse=True)
def _clean_pending():
    M._TSELL_PENDING.clear()
    yield
    M._TSELL_PENDING.clear()


@pytest.mark.parametrize("fmt", ["digits", "dashed"])
def test_settle_tsell_pending_settles_with_both_timestamp_formats(fmt):
    """当日 bar（两种格式）→ 超观察期且无放量新高 → **真的结算**（旧代码恒 continue）。"""
    ts = DIGITS if fmt == "digits" else DASHED
    bars = [{"time": ts, "close": 9.5, "vol": 100.0, "low": 9.4}]
    updates, gw, pending = _settle(bars)
    assert updates, "撤销式T出未结算（当日 bar 又被过滤掉了）"
    (args, kwargs) = updates[0]
    assert args[0] == 7 and args[1] == "executed"
    assert gw.call_count == 1
    assert gw.call_args[0][0] == "SH600000" and gw.call_args[0][1] == "sell"
    assert "SH600000" not in pending


def test_settle_tsell_pending_undo_still_works():
    """放量过前高 → 撤销（status=cancelled），别把真实撤销语义改坏。"""
    bars = [{"time": DIGITS, "close": 10.5, "vol": 500.0, "low": 9.4}]
    updates, gw, pending = _settle(bars)
    assert updates and updates[0][0][1] == "cancelled"
    assert gw.call_count == 0
    assert "SH600000" not in pending


def test_settle_tsell_pending_ignores_previous_day_bars():
    """只有前一交易日的 bar → 不结算（pending 保留，等下一轮）。"""
    bars = [{"time": YDAY_DIGITS, "close": 9.5, "vol": 100.0, "low": 9.4},
            {"time": YDAY_DASHED, "close": 9.6, "vol": 100.0, "low": 9.4}]
    updates, gw, pending = _settle(bars)
    assert updates == [] and gw.call_count == 0
    assert "SH600000" in pending


def test_settle_tsell_pending_takes_todays_last_bar_not_yesterdays():
    """旧写法若命中了前一日 bar（带横线场景下的错位）会误判 undo → 必须只认当日最后一根。"""
    bars = [{"time": DIGITS, "close": 9.5, "vol": 100.0, "low": 9.4},
            {"time": YDAY_DASHED, "close": 11.0, "vol": 900.0, "low": 9.4}]
    updates, gw, pending = _settle(bars)
    assert updates and updates[0][0][1] == "executed", "昨日 bar 被当成当日 bar → 误撤销"
    assert gw.call_count == 1


def test_settle_tsell_pending_waits_within_observation_window():
    """观察期内（未超 TSELL_DELAY_S）不动作。"""
    bars = [{"time": DIGITS, "close": 9.5, "vol": 100.0, "low": 9.4}]
    updates, gw, pending = _settle(bars, pending_ts_offset=5)
    assert updates == [] and gw.call_count == 0
    assert "SH600000" in pending


# ────────────────────────── ③ 分时企稳（fail-open 硬伤）──────────────────────────

def _patch_bars(monkeypatch, bars):
    monkeypatch.setattr("app.services.t_data_sources.fetch_minute_bars",
                        lambda symbol, freq="m1", count=120: bars)


@pytest.mark.parametrize("ts", [DIGITS, "202609170940", DASHED, "2026-09-17 09:40:00"])
def test_stabilize_not_new_low_at_false_when_today_makes_new_low(monkeypatch, ts):
    """当日确实创新低 → False（旧代码恒 True，企稳形同虚设）。"""
    _patch_bars(monkeypatch, [{"time": ts, "low": 9.0, "close": 9.1}])
    assert M.stabilize_not_new_low_at("SH600000", 8.80, NOW) is False


@pytest.mark.parametrize("ts", [DIGITS, DASHED])
def test_stabilize_not_new_low_at_true_when_not_new_low(monkeypatch, ts):
    _patch_bars(monkeypatch, [{"time": ts, "low": 9.0, "close": 9.4}])
    assert M.stabilize_not_new_low_at("SH600000", 9.50, NOW) is True


def test_stabilize_not_new_low_at_uses_today_low_not_yesterday(monkeypatch):
    """日低只看当日：昨日低点 8.0 不该把当日 9.5 判成"创新低"。"""
    _patch_bars(monkeypatch, [{"time": YDAY_DIGITS, "low": 8.0, "close": 8.1},
                              {"time": DIGITS, "low": 9.4, "close": 9.5}])
    assert M.stabilize_not_new_low_at("SH600000", 9.50, NOW) is True


def test_stabilize_not_new_low_at_no_today_bars_still_passes(monkeypatch):
    """无当日分钟线时保持原有 fail-open（有腾讯 qt 兜底）——这是既定设计，不是缺陷。"""
    _patch_bars(monkeypatch, [{"time": YDAY_DIGITS, "low": 8.0, "close": 8.1}])
    assert M.stabilize_not_new_low_at("SH600000", 9.50, NOW) is True


# ────────────────────────── ④ minute.m1.low_today ──────────────────────────

@pytest.mark.parametrize("ts", [DIGITS, DASHED])
def test_build_minute_snapshot_low_today(monkeypatch, ts):
    m1 = [{"time": ts, "low": 9.1, "close": 9.2},
          {"time": ts, "low": 8.9, "close": 9.0}]
    m5 = [{"time": ts, "low": 8.9, "close": 9.0, "vol": 1.0}]
    monkeypatch.setattr("app.services.t_data_sources.fetch_minute_bars",
                        lambda symbol, freq="m1", count=120: m1 if freq == "m1" else m5)
    mon = M.TMonitor.__new__(M.TMonitor)
    mon._stabilize_not_new_low = lambda symbol, current: True
    snap = mon._build_minute_snapshot("SH600000", {"current": 9.0})
    assert snap["m1"]["low_today"] == 8.9, "low_today 恒 0.0 的硬伤没修好"


def test_build_minute_snapshot_low_today_ignores_yesterday(monkeypatch):
    m1 = [{"time": YDAY_DIGITS, "low": 5.0, "close": 5.1},
          {"time": DIGITS, "low": 9.3, "close": 9.4}]
    monkeypatch.setattr("app.services.t_data_sources.fetch_minute_bars",
                        lambda symbol, freq="m1", count=120: m1 if freq == "m1" else [])
    mon = M.TMonitor.__new__(M.TMonitor)
    mon._stabilize_not_new_low = lambda symbol, current: True
    snap = mon._build_minute_snapshot("SH600000", {"current": 9.4})
    assert snap["m1"]["low_today"] == 9.3


# ────────────────────────── ⑤ 指数盘中回撤（旧写法 %Y%m%d，两个方向都踩）──────────

@pytest.mark.parametrize("ts", [DIGITS, DASHED])
def test_index_intraday_dd_uses_today_bars(monkeypatch, ts):
    # 当日 12 根 m5：最高 100 → 收 97 → 回撤 3.0%
    bars = [{"time": ts, "high": 100.0, "close": 100.0}] + \
           [{"time": ts, "high": 100.0, "close": 97.0} for _ in range(11)]
    monkeypatch.setattr("app.services.t_data_sources.fetch_tencent_mkline",
                        lambda symbol, freq="m5", count=60: bars)
    monkeypatch.setattr(M, "datetime", _PinnedDT)
    mon = M.TMonitor.__new__(M.TMonitor)
    M._index_dd_cache.update({"at": 0.0, "value": 0.0})
    assert mon._index_intraday_dd() == pytest.approx(3.0)

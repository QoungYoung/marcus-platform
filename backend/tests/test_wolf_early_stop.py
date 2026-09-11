# -*- coding: utf-8 -*-
"""① 建仓初期波段逻辑止损 + ④ 趋势中段(暂用 stop_loss_price) 的阶段化解析测试。

纯函数测试，不需要数据库 / 行情。
狼大原话: 2026-03-05「13日内跌破波段低点的-3%没有收回 直接止损」;
          2026-03-06「已经成为趋势后…这个就没意义了…用趋势线的方法…不是一个策略用到底的」。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import wolf_early_stop as W  # noqa: E402


def make_bars(n, start_low=10.0, start="20260801"):
    """造 n 根连续日K（日期用 8 位串按自然日递增即可），low 递增便于定位最低点。"""
    import datetime as dt
    d0 = dt.date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    out = []
    for i in range(n):
        d = (d0 + dt.timedelta(days=i)).strftime("%Y%m%d")
        out.append({"date": d, "close": start_low + i * 0.1,
                    "high": start_low + i * 0.1 + 0.2,
                    "low": start_low + i * 0.1 - 0.1, "vol": 1000.0})
    return out


@pytest.fixture(autouse=True)
def _clean_env():
    keys = ["WOLF_EARLY_STOP", "WOLF_EARLY_STOP_DAYS", "WOLF_SWING_LOW_WIN", "WOLF_EARLY_STOP_PCT"]
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── swing_low_asof: 以建仓日为锚（⑤ 止损预设 = 锁定时点） ──

class TestSwingLow:
    def test_low_is_min_of_window_before_buy(self):
        bars = make_bars(30, start="20260801")
        # 建仓日 = 第 15 根(20260815)
        sl = W.swing_low_asof(bars, "20260815", win=13)
        # 建仓日(index 14)之前 13 根 = index 2..14 → low 最小在 index 2
        assert sl == pytest.approx(10.0 + 2 * 0.1 - 0.1)

    def test_does_not_roll_with_later_bars(self):
        """**关键**: 建仓日之后的下跌不能改变波段低点 —— 否则就是每天滚动的假"预设"。"""
        bars = make_bars(30, start="20260801")
        sl1 = W.swing_low_asof(bars, "20260815", win=13)
        # 追加建仓日之后的一次深跌
        bars2 = bars + [{"date": "20260820", "close": 5.0, "high": 5.1, "low": 4.0, "vol": 999.0}]
        sl2 = W.swing_low_asof(bars2, "20260815", win=13)
        assert sl1 == sl2

    def test_insufficient_bars_returns_none(self):
        bars = make_bars(3, start="20260801")
        assert W.swing_low_asof(bars, "20260803", win=13) is None

    def test_no_buy_date_returns_none(self):
        bars = make_bars(30)
        assert W.swing_low_asof(bars, None, win=13) is None
        assert W.swing_low_asof(bars, "", win=13) is None

    def test_date_format_tolerant(self):
        bars = make_bars(30, start="20260801")
        assert W.swing_low_asof(bars, "2026-08-15", win=13) == W.swing_low_asof(bars, "20260815", win=13)


# ── held_trading_days ──

class TestHeldDays:
    def test_counts_bars_after_buy(self):
        bars = make_bars(30, start="20260801")   # 20260801 + 29 天
        assert W.held_trading_days(bars, "20260801") == 29
        assert W.held_trading_days(bars, "20260815") == 15

    def test_buy_date_not_in_bars(self):
        bars = make_bars(30, start="20260801")
        assert W.held_trading_days(bars, "20260810") == 20

    def test_none_without_buy_date(self):
        assert W.held_trading_days(make_bars(10), None) is None


# ── resolve_stop: 阶段切换 ──

class TestResolveStop:
    def test_trend_stage_uses_stop_loss_price(self):
        bars = make_bars(40, start="20260801")
        stop, src, why = W.resolve_stop(9.5, bars, "20260801")   # 持有 39 交易日 > 13
        assert stop == 9.5
        assert src == "stop_loss_price"
        assert "已成趋势" in why

    def test_boundary_exactly_13_is_early(self):
        bars = make_bars(30, start="20260801")
        # 建仓 20260816 → 之后 14 根 = 20260817..20260830
        assert W.held_trading_days(bars, "20260816") == 14
        # 建仓 20260817 → 之后 13 根
        assert W.held_trading_days(bars, "20260817") == 13
        stop, src, _ = W.resolve_stop(9.5, bars, "20260817")
        assert src == "wolf_early_swing"      # 13 <= 13 → 仍属建仓初期
        stop2, src2, _ = W.resolve_stop(9.5, bars, "20260816")
        assert src2 == "stop_loss_price"      # 14 > 13 → 已成趋势

    def test_early_stage_stop_is_low_minus_3pct(self):
        bars = make_bars(30, start="20260801")
        sl = W.swing_low_asof(bars, "20260817", win=13)
        stop, src, why = W.resolve_stop(9.5, bars, "20260817")
        assert src == "wolf_early_swing"
        assert stop == pytest.approx(round(sl * 0.97, 3))
        assert "波段低点" in why and "2026-03-05" in why
        # 与既有 stop_loss_price **不做复合**（狼大: 不是一个策略用到底的）
        assert stop != 9.5 or sl * 0.97 == 9.5

    def test_early_stage_without_cond_stop_still_produces_stop(self):
        """switch 腿建的条件没有 stop_loss_price —— 建仓初期仍应给出结构止损（原缺口）。"""
        bars = make_bars(30, start="20260801")
        stop, src, _ = W.resolve_stop(None, bars, "20260817")
        assert src == "wolf_early_swing" and stop and stop > 0

    def test_insufficient_history_falls_back(self):
        bars = make_bars(4, start="20260801")
        stop, src, why = W.resolve_stop(9.5, bars, "20260803")
        assert src == "stop_loss_price" and stop == 9.5
        assert "数据不足" in why

    def test_no_buy_date_falls_back(self):
        stop, src, why = W.resolve_stop(9.5, make_bars(30), None)
        assert src == "stop_loss_price" and stop == 9.5
        assert "无建仓日" in why

    def test_disabled_switch(self):
        os.environ["WOLF_EARLY_STOP"] = "0"
        stop, src, why = W.resolve_stop(9.5, make_bars(30, start="20260801"), "20260817")
        assert src == "stop_loss_price" and stop == 9.5
        assert "WOLF_EARLY_STOP=0" in why

    def test_no_stop_at_all_when_nothing_available(self):
        stop, src, _ = W.resolve_stop(None, make_bars(30, start="20260801"), "20260801")
        assert stop is None and src == "none"

    def test_early_days_configurable(self):
        bars = make_bars(30, start="20260801")
        os.environ["WOLF_EARLY_STOP_DAYS"] = "5"
        _, src, _ = W.resolve_stop(9.5, bars, "20260817")   # 持有 13 > 5
        assert src == "stop_loss_price"

    def test_stop_pct_configurable(self):
        bars = make_bars(30, start="20260801")
        sl = W.swing_low_asof(bars, "20260817", win=13)
        os.environ["WOLF_EARLY_STOP_PCT"] = "5"
        stop, src, _ = W.resolve_stop(9.5, bars, "20260817")
        assert src == "wolf_early_swing"
        assert stop == pytest.approx(round(sl * 0.95, 3))

    def test_swing_win_configurable(self):
        bars = make_bars(30, start="20260801")
        sl13 = W.swing_low_asof(bars, "20260817", win=13)
        os.environ["WOLF_SWING_LOW_WIN"] = "6"
        sl6 = W.swing_low_asof(bars, "20260817", win=6)
        assert sl6 >= sl13          # 窗口更短 → 低点不会更低


# ── 建仓日锚点（DB 不可用时必须安全退化） ──

class TestFirstBuyDate:
    @pytest.mark.skipif(not os.getenv("MARCUS_DB_TEST"), reason="需可达数据库(本机 PG 不通时该用例会长时间等待)")
    def test_does_not_raise_without_db(self):
        v = W.first_buy_date("t", "SH600000")
        assert v is None or isinstance(v, str)

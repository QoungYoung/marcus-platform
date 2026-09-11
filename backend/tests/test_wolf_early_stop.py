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
    keys = ["WOLF_EARLY_STOP", "WOLF_EARLY_STOP_DAYS", "WOLF_SWING_LOW_WIN", "WOLF_EARLY_STOP_PCT",
            "WOLF_NEG_EVENT", "WOLF_NEG_EVENT_DAYS",
            "WOLF_LOGIC_TIME_STOP", "WOLF_LOGIC_TIME_STOP_DAYS", "WOLF_SWING_HIGH_WIN",
            "WOLF_DATED_LIVE_FALLBACK"]
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

# ── 「无利空」前提（狼大 2026-03-05 原话） ──

@pytest.fixture()
def negdir(tmp_path, monkeypatch):
    """把 DATA_DIR 指到临时目录, 用于 wolf_negative_events.json 的读写。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def write_neg(tmp_path, obj):
    import json
    (tmp_path / W.NEG_EVENT_FILE).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class TestNegativeEvent:
    def test_no_file_returns_none(self, negdir):
        assert W.negative_event("SH600000", buy_date="20260801", today="20260810") is None

    def test_in_holding_window(self, negdir):
        write_neg(negdir, {"SH600000": {"date": "20260805", "note": "突发利空"}})
        r = W.negative_event("SH600000", buy_date="20260801", today="20260810")
        assert r and r["date"] == "20260805" and "突发" in r["note"]

    def test_before_buy_date_ignored(self, negdir):
        """建仓**前**就存在的利空不属于"持有期内的意外事件"（那种应在建仓决策层解决）。"""
        write_neg(negdir, {"SH600000": {"date": "20260701"}})
        assert W.negative_event("SH600000", buy_date="20260801", today="20260810") is None

    def test_expired_ignored(self, negdir):
        write_neg(negdir, {"SH600000": {"date": "20260805"}})
        assert W.negative_event("SH600000", buy_date="20260801", today="20260901") is None   # 27 天 > 13
        assert W.negative_event("SH600000", buy_date="20260801", today="20260815") is not None

    def test_missing_date_ignored(self, negdir):
        write_neg(negdir, {"SH600000": {"note": "无日期"}})
        assert W.negative_event("SH600000", buy_date="20260801") is None

    def test_symbol_format_tolerant(self, negdir):
        write_neg(negdir, {"600000.SH": {"date": "20260805"}})
        assert W.negative_event("SH600000", buy_date="20260801", today="20260810") is not None

    def test_switch_off_ignores(self, negdir):
        write_neg(negdir, {"SH600000": {"date": "20260805"}})
        os.environ["WOLF_NEG_EVENT"] = "0"
        assert W.negative_event("SH600000", buy_date="20260801", today="20260810") is None

    def test_corrupt_file_fails_open(self, negdir):
        (negdir / W.NEG_EVENT_FILE).write_text("{not json", encoding="utf-8")
        assert W.negative_event("SH600000", buy_date="20260801", today="20260810") is None


class TestNegativeEventExemptsEarlyStop:
    """有意外事件/黑天鹅 → **不套** ① 结构线, 退回 stop_loss_price（狼大: 那是"别的逻辑"）。"""

    def test_neg_event_falls_back_to_stop_loss_price(self):
        bars = make_bars(30, start="20260801")
        stop, src, why = W.resolve_stop(9.5, bars, "20260817",
                                        neg_event={"date": "20260818", "note": "黑天鹅"})
        assert (stop, src) == (9.5, "neg_event")
        assert "别的逻辑" in why and "20260818" in why

    def test_without_neg_event_uses_swing_stop(self):
        bars = make_bars(30, start="20260801")
        _, src, _ = W.resolve_stop(9.5, bars, "20260817", neg_event=None)
        assert src == "wolf_early_swing"

    def test_neg_event_never_invents_a_stop(self):
        """没有 cond_stop 又没有结构线 → 返回 none（不能凭空造一条止损线）。"""
        bars = make_bars(30, start="20260801")
        stop, src, _ = W.resolve_stop(None, bars, "20260817", neg_event={"date": "20260818"})
        assert stop is None and src == "none"

    def test_neg_event_does_not_apply_after_trend_stage(self):
        """已成趋势时本来就走 stop_loss_price, 利空标记不改变结果（前提只作用于 ① 那一层）。"""
        bars = make_bars(40, start="20260801")
        stop, src, _ = W.resolve_stop(9.5, bars, "20260801", neg_event={"date": "20260810"})
        assert (stop, src) == (9.5, "stop_loss_price")

    def test_symbol_lookup_path(self, negdir):
        write_neg(negdir, {"SH600017": {"date": "20260818", "note": "黑天鹅"}})
        bars = make_bars(30, start="20260801")
        stop, src, _ = W.resolve_stop(9.5, bars, "20260817", symbol="SH600017", today="20260818")
        assert (stop, src) == (9.5, "neg_event")

    def test_symbol_lookup_absent_behaves_normally(self, negdir):
        bars = make_bars(30, start="20260801")
        _, src, _ = W.resolve_stop(9.5, bars, "20260817", symbol="SH600017", today="20260818")
        assert src == "wolf_early_swing"

    def test_switch_off_ignores_neg_event_file(self, negdir):
        write_neg(negdir, {"SH600017": {"date": "20260818"}})
        os.environ["WOLF_NEG_EVENT"] = "0"
        bars = make_bars(30, start="20260801")
        _, src, _ = W.resolve_stop(9.5, bars, "20260817", symbol="SH600017", today="20260818")
        assert src == "wolf_early_swing"

# ── ① 后半句：13 日内未碰新高 → 「逻辑与时间」离场（狼大 2026-03-05 同一句原话） ──

def bars_with_high(n, buy_idx, prior_high=10.0, after_high=None, start="20260801"):
    """造 n 根日K；前高由 buy_idx 之前的 high 决定；after_high 指定建仓后最高价。"""
    import datetime as dt
    d0 = dt.date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    out = []
    for i in range(n):
        d = (d0 + dt.timedelta(days=i)).strftime("%Y%m%d")
        hi = prior_high if i <= buy_idx else (after_high if after_high is not None else prior_high - 0.5)
        out.append({"date": d, "close": hi - 0.2, "high": hi, "low": hi - 0.6, "vol": 1000.0})
    return out


class TestMadeNewHigh:
    def test_front_high_is_max_of_window_before_buy(self):
        bars = bars_with_high(20, buy_idx=9, prior_high=12.0)
        assert W.swing_high_asof(bars, bars[9]["date"], win=13) == pytest.approx(12.0)

    def test_touch_counts_not_only_strict_break(self):
        """狼大「**碰新高或者新高**」—— 碰到即算，不要求严格突破。"""
        bars = bars_with_high(20, buy_idx=9, prior_high=12.0, after_high=12.0)
        assert W.made_new_high(bars, bars[9]["date"], win=13, days=13) is True

    def test_window_incomplete_returns_none(self):
        """窗口没走完（持有 < 13 交易日）→ 不动作（不能买三天没新高就卖）。"""
        bars = bars_with_high(15, buy_idx=9, prior_high=12.0)
        assert W.made_new_high(bars, bars[9]["date"], win=13, days=13) is None

    def test_window_elapsed_no_high_returns_false(self):
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0)
        assert W.made_new_high(bars, bars[9]["date"], win=13, days=13) is False

    def test_intraday_high_counts(self):
        """盘中最高必须计入 —— 否则窗口最后一天的盘中触碰会漏判。"""
        bars = bars_with_high(24, buy_idx=9, prior_high=12.0)   # 建仓后 14 根 > 13 → 窗口已走完
        assert W.made_new_high(bars, bars[9]["date"], win=13, days=13) is False
        assert W.made_new_high(bars, bars[9]["date"], win=13, days=13, extra_high=12.0) is True

    def test_insufficient_prior_bars_returns_none(self):
        bars = bars_with_high(4, buy_idx=2, prior_high=12.0)
        assert W.made_new_high(bars, bars[2]["date"], win=13, days=13) is None

    def test_no_buy_date_returns_none(self):
        assert W.made_new_high(bars_with_high(20, 9), None, win=13, days=13) is None


class TestLogicTimeStop:
    def test_exit_when_window_elapsed_without_new_high(self):
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0)
        ok, why = W.logic_time_stop(bars, bars[9]["date"])
        assert ok is True
        assert "2026-03-05" in why and "从未碰过前高" in why

    def test_hold_when_new_high_made(self):
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0, after_high=12.5)
        ok, why = W.logic_time_stop(bars, bars[9]["date"])
        assert ok is False and "逻辑成立" in why

    def test_hold_while_window_still_open(self):
        bars = bars_with_high(15, buy_idx=9, prior_high=12.0)
        ok, why = W.logic_time_stop(bars, bars[9]["date"])
        assert ok is False
        # reason 必须说清是"窗口未走完"，不能与"前高算不出"混为一谈（生产排查踩过）
        assert "观察窗未走完" in why and "数据不足" not in why

    def test_switch_off(self):
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0)
        os.environ["WOLF_LOGIC_TIME_STOP"] = "0"
        ok, why = W.logic_time_stop(bars, bars[9]["date"])
        assert ok is False and "WOLF_LOGIC_TIME_STOP=0" in why

    def test_window_configurable(self):
        bars = bars_with_high(20, buy_idx=9, prior_high=12.0)   # 建仓后 10 根
        assert W.logic_time_stop(bars, bars[9]["date"])[0] is False          # 默认 13 → 未走完
        os.environ["WOLF_LOGIC_TIME_STOP_DAYS"] = "5"
        assert W.logic_time_stop(bars, bars[9]["date"])[0] is True           # 5 → 已走完

    def test_neg_event_exempts(self):
        """黑天鹅导致的不创新高属"别的逻辑" → 不按逻辑时间离场。"""
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0)
        ok, why = W.logic_time_stop(bars, bars[9]["date"],
                                    neg_event={"date": "20260815", "note": "黑天鹅"})
        assert ok is False and "别的逻辑" in why

    def test_no_neg_event_still_exits(self):
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0)
        assert W.logic_time_stop(bars, bars[9]["date"], neg_event=None)[0] is True

    def test_missing_data_does_not_exit(self):
        bars = bars_with_high(4, buy_idx=2, prior_high=12.0)
        ok, why = W.logic_time_stop(bars, bars[2]["date"])
        assert ok is False and "前高数据不足" in why

    def test_front_high_win_configurable(self):
        bars = bars_with_high(30, buy_idx=9, prior_high=12.0, after_high=11.5)
        assert W.logic_time_stop(bars, bars[9]["date"])[0] is True     # 前高 12.0 未碰
        os.environ["WOLF_SWING_HIGH_WIN"] = "5"
        # 窗口缩短 → 前高仍是 12.0（买点前 5 根都是 12.0）→ 仍不碰
        assert W.logic_time_stop(bars, bars[9]["date"])[0] is True


class TestDailyDatedLiveFallback:
    """_daily_dated 的**实时源兜底**（2026-09-11 生产实测后加）。

    生产 data/stock_5m_bt 与 data/recent_sync 是 **2026-09-03 的一次性导出**、无常驻同步任务
    → 当前在册 13 个标的里 0 个新鲜 → 不补齐则 ① 两个机制恒不动作（静默失效）。
    """

    def _mon(self, tmp_path):
        import sys as _s
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parents[2]
        if str(_root / "backend") not in _s.path:
            _s.path.insert(0, str(_root / "backend"))
        os.environ["DATA_DIR"] = str(tmp_path)
        from app.services.t_monitor import TMonitor
        return TMonitor()

    @staticmethod
    def _write_local(tmp_path, code6, dates):
        import json
        d = tmp_path / "stock_5m_bt"
        d.mkdir(exist_ok=True)
        obj = {dt: [{"time": dt + "1000", "close": 10.0, "high": 10.5, "low": 9.5, "vol": 100.0},
                    {"time": dt + "1030", "close": 10.2, "high": 10.8, "low": 9.6, "vol": 120.0}]
               for dt in dates}
        (d / (code6 + ".json")).write_text(json.dumps(obj), encoding="utf-8")

    @staticmethod
    def _stub_live(monkeypatch, bars, calls):
        import app.services.t_build as TB

        def fake(symbol, count=40, as_of=None):
            calls.append(symbol)
            return bars
        monkeypatch.setattr(TB, "_fetch_daily_bars", fake, raising=True)

    def test_stale_local_uses_live(self, tmp_path, monkeypatch):
        today = __import__("datetime").datetime.now().strftime("%Y%m%d")
        self._write_local(tmp_path, "600519", ["20260820", "20260821"])
        calls = []
        live = [{"date": "20260908", "close": 1.0, "high": 1.1, "low": 0.9, "vol": 1.0},
                {"date": "20260909", "close": 2.0, "high": 2.1, "low": 1.9, "vol": 1.0},
                {"date": today, "close": 3.0, "high": 3.1, "low": 2.9, "vol": 1.0}]
        self._stub_live(monkeypatch, live, calls)
        bars = self._mon(tmp_path)._daily_dated("SH600519", 20)
        assert calls == ["SH600519"]                      # 调用了实时源
        assert [b["date"] for b in bars] == ["20260908", "20260909"]   # 今天那根被排除（与本地口径一致）
        assert bars[-1]["high"] == 2.1

    def test_fresh_local_skips_live(self, tmp_path, monkeypatch):
        import datetime as dt
        fresh = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
        self._write_local(tmp_path, "600519", [fresh])
        calls = []
        self._stub_live(monkeypatch, [{"date": "20260101", "close": 1, "high": 1, "low": 1, "vol": 1}], calls)
        bars = self._mon(tmp_path)._daily_dated("SH600519", 20)
        assert calls == []                                # 本地够新 → 不请求
        assert [b["date"] for b in bars] == [fresh]

    def test_switch_off_disables_fallback(self, tmp_path, monkeypatch):
        os.environ["WOLF_DATED_LIVE_FALLBACK"] = "0"
        calls = []
        self._stub_live(monkeypatch, [{"date": "20260909", "close": 1, "high": 1, "low": 1, "vol": 1}], calls)
        assert self._mon(tmp_path)._daily_dated("SH600519", 20) == []
        assert calls == []

    def test_result_is_cached_per_day(self, tmp_path, monkeypatch):
        calls = []
        self._stub_live(monkeypatch, [{"date": "20260909", "close": 1, "high": 1, "low": 1, "vol": 1}], calls)
        mon = self._mon(tmp_path)
        a = mon._daily_dated("SH600519", 20)
        b = mon._daily_dated("SH600519", 20)
        assert calls == ["SH600519"] and a is b

    def test_primary_failure_uses_tencent(self, tmp_path, monkeypatch):
        """主兜底(Tushare/东财)全失败 → 用腾讯日线第三个源（生产实测那两个源会短暂失败）。"""
        import app.services.t_build as TB
        import app.services.t_monitor as TM
        monkeypatch.setattr(TB, "_fetch_daily_bars", lambda symbol, count=40, as_of=None: None, raising=True)
        monkeypatch.setattr(TM, "_fetch_daily_tencent_dated",
                            lambda symbol, count=40: [{"date": "20260909", "open": 1, "close": 1,
                                                       "high": 1.1, "low": 0.9, "vol": 1.0}], raising=True)
        bars = self._mon(tmp_path)._daily_dated("SH600519", 20)
        assert [b["date"] for b in bars] == ["20260909"] and bars[0]["high"] == 1.1

    def test_all_sources_down_falls_back_to_local(self, tmp_path, monkeypatch):
        """三个源全失败 → 用本地缓存，不抛、不误判。"""
        import app.services.t_build as TB
        import app.services.t_monitor as TM
        self._write_local(tmp_path, "600519", ["20260820"])
        monkeypatch.setattr(TB, "_fetch_daily_bars", lambda symbol, count=40, as_of=None: None, raising=True)
        monkeypatch.setattr(TM, "_fetch_daily_tencent_dated", lambda symbol, count=40: None, raising=True)
        bars = self._mon(tmp_path)._daily_dated("SH600519", 20)
        assert [b["date"] for b in bars] == ["20260820"]

    def test_primary_source_raises_still_tries_tencent(self, tmp_path, monkeypatch):
        """主源抛异常也不能中断链条 —— 继续走腾讯源。"""
        import app.services.t_build as TB
        import app.services.t_monitor as TM
        monkeypatch.setattr(TB, "_fetch_daily_bars",
                            lambda symbol, count=40, as_of=None: (_ for _ in ()).throw(RuntimeError("boom")),
                            raising=True)
        monkeypatch.setattr(TM, "_fetch_daily_tencent_dated",
                            lambda symbol, count=40: [{"date": "20260909", "open": 1, "close": 1,
                                                       "high": 1.2, "low": 0.9, "vol": 1.0}], raising=True)
        bars = self._mon(tmp_path)._daily_dated("SH600519", 20)
        assert [b["date"] for b in bars] == ["20260909"] and bars[0]["high"] == 1.2

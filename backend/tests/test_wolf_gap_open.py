# -*- coding: utf-8 -*-
"""A6/A8/A9-前半 高低开+跳空缺口+量能实时对比（2026-09-11）单测。

狼大原文（XLS 2025-04-15「我的买卖做T方法」条件4，逐字）：
  「如果是低开，缩量，黄线高于白线，**在不破指数下支撑的前提下**这个现象1，
    及如果是高开，放量，黄线高于白线，**在不接近上方压力位**，及**开盘15分钟后不跌破当日
    高开的跳空缺口下方(也就是昨天最高位)**这个现象2。这两个情况加仓做进攻板块的成功率会比较高。」
  「条件4：量能，…一定要随时看股票软件里面 **与上一日的量能实时对比**。」
  「如果当出现以下情况时，操作谨慎，尽量不要加仓进场：1 黄线迅速下穿白线，放量。
    2 **黄白线交织，缩大量**。3 白线迅速上穿黄线：缩量，4 接近大指数级别压力位附近」

口径要点：缺口下沿=**昨日最高价**；量能=**今日累计/昨日同期累计**。
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_gap_open as G  # noqa: E402
from app.services import wolf_index_breadth as HB  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in ("WOLF_GAP_OPEN", "WOLF_GAP_CAUTION", "WOLF_GO_VOL_EPS", "WOLF_GO_NEAR_PRESSURE_PCT",
              "WOLF_GO_ALLOW_STALE", "WOLF_GO_WHIPSAW_VOL", "WOLF_HB_CROSS_WIN", "WOLF_HB_CROSS_EPS",
              "WOLF_GO_PRESSURE_WIN"):
        monkeypatch.delenv(k, raising=False)
    G._D_CACHE.update({"at": 0.0, "value": None})
    G._M5_CACHE.update({"at": 0.0, "value": None})
    HB._CACHE.update({"at": 0.0, "value": None})
    HB._hist().clear()
    yield
    HB._hist().clear()


def _m5day(d8, bars=48, base=100.0, vols=None, first_open=None, lows=None):
    """造一天 m5，**与腾讯真实时间轴一致**：09:35–11:30（24 根）+ 13:05–15:00（24 根）= 48 根。

    （bar 的 time 是**该 5 分钟结束时**刻，首根 09:35 —— 首版夹具从 09:30 起、末根跑到 15:55，错了。）
    """
    full = []
    for h, m, n in ((9, 35, 24), (13, 5, 24)):
        for _ in range(n):
            full.append(f"{h:02d}{m:02d}")
            m += 5
            if m >= 60:
                h, m = h + 1, m - 60
    full = full[:bars]
    out = []
    for i, hm in enumerate(full):
        o = first_open if (i == 0 and first_open is not None) else base
        lo = base - 0.5 if lows is None else lows[i]
        out.append({"time": f"{d8}{hm}", "open": o, "close": base + 0.1, "high": base + 0.5,
                    "low": lo, "vol": (vols[i] if vols else 1000.0)})
    return out


def _daily(rows):
    return [{"trade_date": d, "open": o, "close": c, "high": hi, "low": lo, "vol": v}
            for d, o, c, hi, lo, v in rows]


class TestVolumeRatio:
    def test_ratio_uses_prev_day_same_time(self):
        """条件4 口径 = 与**上一日同时刻**累计量对比（不是与上一日全天对比）。"""
        d1 = _m5day("20260910", vols=[1000.0] * 48)
        d2 = _m5day("20260911", vols=[2000.0] * 48)
        r = G.volume_ratio(d1 + d2, date8="20260911")
        assert r["ratio"] == 2.0 and r["kind"] == "expand"
        assert r["at"] == "09-11 15:00" and r["prev_date"] == "20260910"

    def test_partial_day_aligns_by_clock(self):
        """盘中只有前半段 bar 时，昨日只取到同一 HHMM —— 否则会拿全天量去比半天量。"""
        d1 = _m5day("20260910", vols=[1000.0] * 48)
        d2 = _m5day("20260911", bars=12, vols=[1000.0] * 12)     # 只到 10:25
        r = G.volume_ratio(d1 + d2, date8="20260911")
        # 今日 12 根 ×1000；昨日同期也只取 12 根 ×1000
        assert r["today_cum"] == pytest.approx(12000.0)
        assert r["prev_cum"] == pytest.approx(12000.0)
        assert r["ratio"] == pytest.approx(1.0) and r["kind"] == "flat"

    def test_shrink_flat_expand_thresholds(self):
        d1 = _m5day("20260910", vols=[1000.0] * 48)
        for mult, kind in ((0.5, "shrink"), (1.0, "flat"), (1.5, "expand")):
            d2 = _m5day("20260911", vols=[1000.0 * mult] * 48)
            assert G.volume_ratio(d1 + d2, date8="20260911")["kind"] == kind

    def test_single_day_returns_none(self):
        assert G.volume_ratio(_m5day("20260911"), date8="20260911") is None


class TestDayGuard:
    def test_stale_last_day_is_refused(self, monkeypatch):
        """m5 最后一根是**上一交易日**时不得当成"今日"（否则拿昨天的缺口给今天做门）。"""
        bars = _m5day("20200101") + _m5day("20200102")
        monkeypatch.setattr(G, "index_m5", lambda force=False: bars)
        monkeypatch.setattr(G, "index_daily", lambda force=False: _daily([
            ("20200101", 100, 100, 101, 99, 1), ("20200102", 100, 100, 101, 99, 1)]))
        assert G.volume_ratio() is None          # 非今日 → 不判
        assert G.gap_state() is None
        monkeypatch.setenv("WOLF_GO_ALLOW_STALE", "1")
        assert G.volume_ratio() is not None      # 显式允许时才用

    def test_explicit_date_bypasses_guard(self):
        """显式 date8（回放用）不受"必须是今天"的守卫限制；但仍需窗口内有前一日。"""
        bars = _m5day("20260909") + _m5day("20260910") + _m5day("20260911")
        assert G.volume_ratio(bars, date8="20260910") is not None
        assert G.volume_ratio(bars, date8="20260909") is None     # 窗口内没有更早的一天


class TestGapState:
    def _mk(self, today_open, prev=(3940.0, 3930.0, 3950.0, 3920.0)):
        """prev = (open, close, high, low)"""
        d = _daily([("20260910", prev[0], prev[1], prev[2], prev[3], 1)])
        m = _m5day("20260910") + _m5day("20260911", first_open=today_open)
        return G.gap_state(daily=d, m5=m, date8="20260911")

    def test_gap_up_lower_bound_is_prev_high(self):
        """他原话括注「也就是**昨天最高位**」→ 缺口下沿必须等于昨日最高价。"""
        g = self._mk(3960.0)
        assert g["kind"] == "gap_up"
        assert g["gap_low"] == 3950.0 and g["gap_high"] == 3960.0

    def test_low_open(self):
        assert self._mk(3925.0)["kind"] == "low_open"

    def test_flat_when_between_prev_close_and_prev_high(self):
        assert self._mk(3945.0)["kind"] == "flat"

    def test_gap_broken_after_0945(self):
        """现象2 要求"开盘15分钟后不跌破缺口下沿"→ 09:45 前破不算（他的措辞是"开盘15分钟后"）。"""
        d = _daily([("20260910", 3940.0, 3930.0, 3950.0, 3920.0, 1)])
        lows = [3955.0] * 48
        lows[5] = 3940.0        # 10:00 那根破缺口（09:45 之后）
        m = _m5day("20260910") + _m5day("20260911", first_open=3960.0, lows=lows)
        g = G.gap_state(daily=d, m5=m, date8="20260911")
        assert g["gap_broken"] is True and g["low_after_0945"] == 3940.0

    def test_gap_broken_before_0945_ignored(self):
        d = _daily([("20260910", 3940.0, 3930.0, 3950.0, 3920.0, 1)])
        lows = [3955.0] * 48
        lows[1] = 3940.0        # 09:40 → 不算（还没到"15分钟后"）
        m = _m5day("20260910") + _m5day("20260911", first_open=3960.0, lows=lows)
        g = G.gap_state(daily=d, m5=m, date8="20260911")
        assert g["gap_broken"] is False


class TestCaution:
    """谨慎 4 条（"尽量不加仓进场"）。"""

    def test_1_huang_down_cross_with_expand(self):
        r = G.caution_reasons(gap={"last": 100.0}, vol={"kind": "expand", "ratio": 1.3},
                             levels={"resistance": 200.0}, cross="down")
        assert any("谨慎1" in x for x in r)
        assert not any("谨慎3" in x for x in r)

    def test_3_cross_with_shrink(self):
        r = G.caution_reasons(gap={"last": 100.0}, vol={"kind": "shrink", "ratio": 0.7},
                             levels={"resistance": 200.0}, cross="up")
        assert any("谨慎3" in x for x in r)
        assert not any("谨慎1" in x for x in r)

    def test_2_whipsaw_with_heavy_shrink(self):
        """「黄白线交织，缩大量」= 交织(符号翻转>=2) ∧ 缩量(默认 <=0.8×)。"""
        r = G.caution_reasons(gap={"last": 100.0}, vol={"kind": "shrink", "ratio": 0.7},
                             levels={"resistance": 200.0}, whipsaw=3)
        assert any("谨慎2" in x for x in r)
        # 只是缩量、没交织 → 不触发
        r2 = G.caution_reasons(gap={"last": 100.0}, vol={"kind": "shrink", "ratio": 0.7},
                              levels={"resistance": 200.0}, whipsaw=1)
        assert not any("谨慎2" in x for x in r2)

    def test_4_near_index_resistance(self):
        r = G.caution_reasons(gap={"last": 99.5}, vol={"kind": "flat", "ratio": 1.0},
                             levels={"resistance": 100.0}, )
        assert any("谨慎4" in x for x in r)
        r2 = G.caution_reasons(gap={"last": 95.0}, vol={"kind": "flat", "ratio": 1.0},
                              levels={"resistance": 100.0})
        assert not any("谨慎4" in x for x in r2)

    def test_4_threshold_is_a_parameter(self, monkeypatch):
        """他未给"接近"的数 → 必须是可调参数。"""
        monkeypatch.setenv("WOLF_GO_NEAR_PRESSURE_PCT", "0.1")
        r = G.caution_reasons(gap={"last": 99.5}, vol={"kind": "flat", "ratio": 1.0},
                             levels={"resistance": 100.0})
        assert not any("谨慎4" in x for x in r)

    def test_no_false_caution_when_no_data(self):
        """交叉样本不足（cross/whipsaw=None）→ 绝不触发（fail-open）。"""
        r = G.caution_reasons(gap={"last": 100.0}, vol={"kind": "expand", "ratio": 1.3},
                             levels={"resistance": 200.0}, cross=None, whipsaw=None)
        assert r == []


class TestCrossAndWhipsaw:
    def test_cross_down(self):
        h = HB._hist()
        now = time.time()
        for i, s in enumerate([0.5, 0.4, -0.3, -0.5]):
            h.append((now - 300 + i * 10, s))
        assert HB.cross_recent(now) == "down"

    def test_cross_up(self):
        h = HB._hist()
        now = time.time()
        for i, s in enumerate([-0.5, -0.4, 0.3, 0.5]):
            h.append((now - 300 + i * 10, s))
        assert HB.cross_recent(now) == "up"

    def test_cross_window_excludes_old(self, monkeypatch):
        monkeypatch.setenv("WOLF_HB_CROSS_WIN", "5")     # 5 分钟窗口
        h = HB._hist()
        now = time.time()
        h.append((now - 3000, 0.5))       # 50 分钟前
        h.append((now - 60, -0.5))        # 窗口内
        assert HB.cross_recent(now) is None              # 只看到一个符号 → 无交叉

    def test_whipsaw_counts_flips(self):
        h = HB._hist()
        now = time.time()
        for i, s in enumerate([0.5, -0.5, 0.5, -0.5, 0.5]):
            h.append((now - 200 + i * 10, s))
        assert HB.whipsaw_recent(now) == 4

    def test_insufficient_samples_returns_none(self):
        h = HB._hist()
        now = time.time()
        h.append((now - 10, 0.5))
        assert HB.cross_recent(now) is None and HB.whipsaw_recent(now) is None

    def test_huang_bai_records_history(self, monkeypatch):
        """历史必须在取数成功时自动累积（否则交叉类条件永远是 None = 静默失效）。"""
        import app.services.wolf_index_breadth as _hb
        monkeypatch.setattr(_hb, "_index_pair", lambda: (1.0, 0.0, None))
        n0 = len(HB._hist())
        HB.huang_bai(force=True)
        assert len(HB._hist()) == n0 + 1


class TestEvaluate:
    def _patch(self, monkeypatch, gap, vol, side="huang", levels=None):
        monkeypatch.setattr(G, "gap_state", lambda **kw: gap)
        monkeypatch.setattr(G, "volume_ratio", lambda **kw: vol)
        monkeypatch.setattr(G, "index_levels", lambda force=False: levels or {"support": 3900.0, "resistance": 4100.0, "src": "t"})
        monkeypatch.setattr(G, "hb_state", lambda: {"side": side, "spread": 0.5})

    def test_form1(self, monkeypatch):
        """现象1 = 低开+缩量+黄>白+不破支撑。"""
        self._patch(monkeypatch, {"kind": "low_open", "last": 3950.0}, {"kind": "shrink", "ratio": 0.8})
        ev = G.evaluate()
        assert ev["form1"] is True and ev["form2"] is None and ev["add_ok"] is True

    def test_form1_false_when_below_support(self, monkeypatch):
        self._patch(monkeypatch, {"kind": "low_open", "last": 3800.0}, {"kind": "shrink", "ratio": 0.8})
        assert G.evaluate()["form1"] is False

    def test_form1_false_when_bai_on_top(self, monkeypatch):
        """他条件3：白线在上 → **减少做T**（现象1/2 都要求黄线在上）。"""
        self._patch(monkeypatch, {"kind": "low_open", "last": 3950.0}, {"kind": "shrink", "ratio": 0.8}, side="bai")
        assert G.evaluate()["form1"] is False

    def test_form2_requires_expand_and_gap_hold(self, monkeypatch):
        self._patch(monkeypatch, {"kind": "gap_up", "last": 4000.0, "gap_broken": False},
                    {"kind": "expand", "ratio": 1.3})
        ev = G.evaluate()
        assert ev["form2"] is True
        self._patch(monkeypatch, {"kind": "gap_up", "last": 4000.0, "gap_broken": True},
                    {"kind": "expand", "ratio": 1.3})
        assert G.evaluate()["form2"] is False

    def test_form2_false_when_near_pressure(self, monkeypatch):
        self._patch(monkeypatch, {"kind": "gap_up", "last": 4099.0, "gap_broken": False},
                    {"kind": "expand", "ratio": 1.3},
                    levels={"support": 3900.0, "resistance": 4100.0, "src": "t"})
        assert G.evaluate()["form2"] is False

    def test_block_add_only_when_caution_on(self, monkeypatch):
        self._patch(monkeypatch, {"kind": "flat", "last": 4099.0}, {"kind": "flat", "ratio": 1.0},
                    levels={"support": 3900.0, "resistance": 4100.0, "src": "t"})
        assert G.block_reason() is not None            # 谨慎4 命中（距压力 0.02%）
        monkeypatch.setenv("WOLF_GAP_CAUTION", "0")
        assert G.block_reason() is None                # 开关可回退

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("WOLF_GAP_OPEN", "0")
        assert G.evaluate() == {"ok": False, "reason": "disabled"}
        assert G.block_reason() is None


class TestWiredIntoContext:
    def test_discipline_context_includes_gap(self, monkeypatch):
        """接线验证：A6 必须真的进 discipline_context（防有代码无调用点）。"""
        import app.services.wolf_discipline as WD
        monkeypatch.setattr(G, "gap_state", lambda **kw: {"kind": "low_open", "open": 3910.0,
                                                          "prev_high": 3949.0, "gap_broken": False,
                                                          "gap_low": None, "last": 3886.0})
        monkeypatch.setattr(G, "volume_ratio", lambda **kw: {"kind": "expand", "ratio": 1.18})
        monkeypatch.setattr(G, "index_levels", lambda force=False: {"support": 3800.0, "resistance": 4100.0, "src": "t"})
        monkeypatch.setattr(G, "hb_state", lambda: {"side": "bai", "spread": -0.9})
        monkeypatch.setattr(WD, "weekend_de_risk", lambda *a, **k: {"active": False, "directive": ""})
        monkeypatch.setattr(WD, "board_half", lambda *a, **k: {"enabled": False, "active_sells": []})
        monkeypatch.setattr(WD, "profit_take", lambda *a, **k: {"enabled": False, "active_sells": []})
        monkeypatch.setattr(WD, "position_cap", lambda *a, **k: {})
        monkeypatch.setattr("app.services.wolf_index_breadth.directive", lambda *a, **k: "")
        monkeypatch.setattr("app.services.wolf_trade_window.directive", lambda *a, **k: "")
        monkeypatch.setattr("app.services.wolf_limit_ladder.directive", lambda *a, **k: "")
        ctx = WD.discipline_context()
        assert "高低开" in ctx and "1.18" in ctx

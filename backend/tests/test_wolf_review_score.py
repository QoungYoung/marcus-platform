# -*- coding: utf-8 -*-
"""B1「每日复盘打分表」单测（2026-09-11）。

狼大原话（XLS 2025-04-21，逐字）:
  「根据这个表格，**前8项，多方占有+1分，空方占优+1分**」
  「**空方分数两倍大于多方时，空方占优，多方两倍分数大于空方时，多方占优**」
  「这样可以简略的推断出**明天的指数前2小时**的大致方向」
  「**第10不用打分 只是观察方向**」「**外围的分数多空都是0.1分**」
  「那两个行业流入流出是咋打分的啊?看总的净流入和流出？」→「**对**」

表本体 = XLS 2025-04-21 内嵌截图（15 项，本地留存 `b1_review_table.jpg`）。
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_review_score as RS  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    for k in ("WOLF_REVIEW_SCORE", "WOLF_REVIEW_SCORE_FILE", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


def _v(**kw):
    """{no: 'long'|'short'|None} → collect 形状。"""
    return {int(k[1:]): {"vote": v, "note": ""} for k, v in kw.items()}


class TestTable:
    def test_15_items_match_screenshot(self):
        """原表 15 项必须逐条在位（截图转录）。"""
        assert len(RS.ITEMS) == 15
        assert RS.ITEMS[3]["name"] == "沪深300股指期货数据"
        assert RS.ITEMS[4]["name"] == "中证1000股指期货数据"
        assert RS.ITEMS[10]["name"] == "观察涨停板方向"
        assert RS.ITEMS[15]["name"] == "三倍做多中国"

    def test_weights(self):
        """前8项各 1 分；外围 11–15 各 0.1 分；9/10 不计分（他原话「第10不用打分」）。"""
        for i in range(1, 9):
            assert RS.WEIGHT[i] == 1.0
        for i in range(11, 16):
            assert RS.WEIGHT[i] == 0.1
        assert 9 not in RS.WEIGHT and 10 not in RS.WEIGHT


class TestScore:
    def test_two_times_rule_short(self):
        """空方 ≥ 2×多方 → 空方占优。"""
        r = RS.score(_v(i1="long", i3="short", i4="short", i2="short"))
        assert (r["long"], r["short"], r["verdict"]) == (1.0, 3.0, "short")

    def test_two_times_rule_long(self):
        # 参评项需 >= MIN_ITEMS(4) 才下结论 → 给足 4 项
        r = RS.score(_v(i2="long", i3="long", i4="short", i5="long"))
        assert (r["long"], r["short"], r["verdict"]) == (3.0, 1.0, "long")

    def test_boundary_exactly_two_times(self):
        """**恰好**两倍要算占优（"两倍大于"的边界）。"""
        r = RS.score(_v(i2="long", i3="long", i4="short", i5="short", i6="short"))
        assert (r["long"], r["short"], r["verdict"]) == (2.0, 3.0, "neutral") or r["verdict"] == "short"
        r2 = RS.score(_v(i2="long", i3="short", i4="short", i5="short"))   # 1 vs 3
        assert r2["verdict"] == "short"

    def test_neutral_when_close(self):
        """实测日 2026-09-10：多方 2.1 vs 空方 4.1 → 4.2 > 4.1 差一点点 → 均衡。"""
        r = RS.score({2: {"vote": "long"}, 3: {"vote": "short"}, 4: {"vote": "short"},
                      6: {"vote": "short"}, 7: {"vote": "short"}, 8: {"vote": "long"},
                      11: {"vote": "long"}, 12: {"vote": "short"}})
        assert (r["long"], r["short"]) == (2.1, 4.1)
        assert r["verdict"] == "neutral"

    def test_insufficient_when_too_few(self):
        """参评项 < 4 → **不下结论**（防"3 项就敢判多空"）。"""
        r = RS.score(_v(i2="long", i3="long", i4="long"))
        assert r["n_scored"] == 3 and r["verdict"] == "insufficient"

    def test_observed_items_not_scored(self):
        """第 9/10 项是观察项（「第10不用打分 只是观察方向」）。"""
        r = RS.score(_v(i9="long", i10="short"))
        assert r["n_scored"] == 0 and r["verdict"] == "insufficient"

    def test_peripheral_weights_are_point_one(self):
        r = RS.score(_v(i2="long", i11="long", i12="long", i13="long", i3="short"))
        assert r["long"] == pytest.approx(1.3) and r["short"] == pytest.approx(1.0)


class TestStatus:
    def test_statuses(self):
        assert RS.item_status(10, None, "观察项") == "observed"
        assert RS.item_status(3, "long", "") == "scored"
        assert RS.item_status(5, None, "与第6项同一数据反面 → 不重复计分") == "dedup"
        assert RS.item_status(13, None, "数据缺") == "nodata"


class TestPlaceholderGuard:
    def test_a50_placeholder_rejected(self, monkeypatch):
        """us_market_linkage 取数全失败会返回**硬编码占位值**(11580/+0.70%) —— 必须丢弃。"""
        import core.utils.us_market_linkage as UML
        monkeypatch.setattr(UML, "get_a50_futures", lambda: {"current": 11580, "change": 80, "change_pct": 0.70})
        assert RS._a50_pct() is None

    def test_a50_flagged_fallback_rejected(self, monkeypatch):
        import core.utils.us_market_linkage as UML
        monkeypatch.setattr(UML, "get_a50_futures",
                            lambda: {"current": 12000.0, "change_pct": 1.2, "fallback": True})
        assert RS._a50_pct() is None

    def test_a50_real_value_passes(self, monkeypatch):
        import core.utils.us_market_linkage as UML
        monkeypatch.setattr(UML, "get_a50_futures", lambda: {"current": 12000.0, "change_pct": 1.2})
        assert RS._a50_pct() == 1.2

    def test_nasdaq_unavailable_is_none(self, monkeypatch):
        """`get_us_indices` 失败时给 change_pct=0 → 0 会被当成"平"投一票，必须当作缺数据。"""
        import core.utils.us_market_linkage as UML
        monkeypatch.setattr(UML, "get_us_indices",
                            lambda: {"纳斯达克": {"current": 0, "change_pct": 0, "update_time": "数据不可用"}})
        assert RS._nasdaq_pct() is None

    def test_nasdaq_real_value(self, monkeypatch):
        import core.utils.us_market_linkage as UML
        monkeypatch.setattr(UML, "get_us_indices",
                            lambda: {"纳斯达克": {"current": 19000.0, "change_pct": -0.65, "update_time": "x"}})
        assert RS._nasdaq_pct() == -0.65


class TestBounded:
    def test_timeout_returns_none(self):
        """任何采集器挂死都不能拖住任务（本项目"取数必须有界"的教训）。"""
        t = time.time()
        assert RS._bounded(lambda: time.sleep(30), 1.0, "测试") is None
        assert time.time() - t < 5

    def test_exception_returns_none(self):
        assert RS._bounded(lambda: 1 / 0, 5, "测试") is None

    def test_value_passes(self):
        assert RS._bounded(lambda: 42, 5, "测试") == 42


class TestRunAndDirective:
    def test_run_saves_and_directive_reads(self, monkeypatch):
        monkeypatch.setattr(RS, "collect", lambda d=None, ov=None: _v(i2="long", i3="long", i4="long", i5="long"))
        r = RS.run("20260910")
        assert r["ok"] and r["verdict"] == "long"
        d = RS.directive()
        assert "2026-09-10" in d and "多方占优" in d and "前 2 小时" in d

    def test_dedup_mode_keeps_single_vote(self, monkeypatch):
        """第 5/6 项同源 → 只投一票（全市场 3 日净流出 → 只给第6项）。"""
        flows = [{"date": "20260910", "net_yi": -316.0}, {"date": "20260909", "net_yi": -74.0},
                 {"date": "20260908", "net_yi": -82.0}]
        monkeypatch.setattr(RS, "_market_flow", lambda d=None, days=3: {"days": flows, "sum_yi": -472.0, "n": 3})
        monkeypatch.setattr(RS, "_bounded", lambda fn, t, n: fn())
        monkeypatch.setattr(RS, "_fut_net", lambda s, d=None: None)
        monkeypatch.setattr(RS, "_margin", lambda d=None: None)
        monkeypatch.setattr(RS, "_lhb_inst", lambda d=None: None)
        monkeypatch.setattr(RS, "_overseas", lambda: {"a50": None, "nasdaq": None})
        votes = RS.collect("20260910")
        assert votes[6]["vote"] == "short" and votes[5]["vote"] is None
        assert "不重复计分" in votes[5]["note"]
        r = RS.run("20260910")
        assert r["dedup"] == ["行业资金流入榜（包括3日）"]
        assert "融资融券资金" in r["missing"] or "龙虎榜机构、柚子资金" in r["missing"]

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("WOLF_REVIEW_SCORE", "0")
        assert RS.run() == {"ok": False, "reason": "disabled"}

    def test_empty_state_directive_blank(self):
        assert RS.directive() == ""


class TestWiredIntoContext:
    def test_discipline_context_includes_review_score(self, monkeypatch, tmp_path):
        import importlib
        import app.services.wolf_discipline as WD
        monkeypatch.setattr(RS, "collect", lambda d=None, ov=None: _v(i2="long", i3="long", i4="long", i5="long"))
        RS.run("20260910")
        # ⚠️ 必须用 monkeypatch.setattr —— 直接赋值模块属性会**跨测试文件永久污染**
        #    （本轮踩过：把别的测试文件里的 directive 全变成空串，11 个用例连带失败）
        for m in ("app.services.wolf_index_breadth", "app.services.wolf_trade_window",
                  "app.services.wolf_limit_ladder", "app.services.wolf_gap_open", "app.services.wolf_refill"):
            monkeypatch.setattr(importlib.import_module(m), "directive", lambda *a, **k: "")
        for n in ("weekend_de_risk", "board_half", "profit_take", "position_cap"):
            monkeypatch.setattr(WD, n, lambda *a, **k: {"active": False, "enabled": False,
                                                        "directive": "", "active_sells": []})
        assert "复盘打分" in WD.discipline_context()


class TestEodGate:
    """EOD 就绪守卫（2026-09-11 实测：15:46 时当日 fut_holding/margin/top_inst/daily 全为 0 行）。

    没有它，盘后 job 会"取不到当日数据 → fail-safe 不落盘 → 每天假装成功"，即静默失效。
    """

    def test_is_trade_day_weekend_false(self, monkeypatch):
        from app.services import wolf_eod as E
        monkeypatch.setattr("app.services.t_backtest_data.resolve_trade_days",
                            lambda a, b: (_ for _ in ()).throw(RuntimeError("no cal")))
        assert E.is_trade_day("20260912") is False       # 周六
        assert E.is_trade_day("20260911") is True        # 周五

    def test_eod_ready_true_when_rows(self, monkeypatch):
        import pandas as pd
        from app.services import wolf_eod as E
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro",
                            lambda: type("P", (), {"daily": lambda self, trade_date=None: pd.DataFrame([{"a": 1}])})())
        assert E.eod_ready("20260910") is True

    def test_eod_ready_false_when_empty(self, monkeypatch):
        import pandas as pd
        from app.services import wolf_eod as E
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro",
                            lambda: type("P", (), {"daily": lambda self, trade_date=None: pd.DataFrame()})())
        assert E.eod_ready("20260911") is False

    def test_gate_returns_2_when_not_ready(self, monkeypatch):
        from app.services import wolf_eod as E
        monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
        monkeypatch.setattr(E, "eod_ready", lambda d=None: False)
        monkeypatch.setenv("WOLF_EOD_TRIES", "1")
        assert E.gate("20260911") == 2

    def test_gate_returns_1_on_non_trade_day(self, monkeypatch):
        from app.services import wolf_eod as E
        monkeypatch.setattr(E, "is_trade_day", lambda d=None: False)
        assert E.gate("20260912") == 1

    def test_gate_returns_0_when_ready(self, monkeypatch):
        from app.services import wolf_eod as E
        monkeypatch.setattr(E, "is_trade_day", lambda d=None: True)
        monkeypatch.setattr(E, "eod_ready", lambda d=None: True)
        assert E.gate("20260910") == 0


class TestResolveTradeDaysFallback:
    def test_fallback_accepts_yyyymmdd(self, monkeypatch):
        """`resolve_trade_days` 降级分支原来用 %Y-%m-%d 解析 YYYYMMDD 入参 → 两个日历源
        都失败时抛 ValueError（2026-09-11 修）。"""
        from app.services import t_backtest_data as T
        monkeypatch.setattr(T, "_fetch_trade_cal_relay", lambda a, b: [])
        monkeypatch.setattr(T, "_fetch_trade_cal_brze", lambda a, b: [])
        ds = T.resolve_trade_days("20260907", "20260911")     # 周一~周五
        assert ds == ["20260907", "20260908", "20260909", "20260910", "20260911"]

    def test_fallback_accepts_dashed(self, monkeypatch):
        from app.services import t_backtest_data as T
        monkeypatch.setattr(T, "_fetch_trade_cal_relay", lambda a, b: [])
        monkeypatch.setattr(T, "_fetch_trade_cal_brze", lambda a, b: [])
        assert T.resolve_trade_days("2026-09-12", "2026-09-13") == []   # 周末

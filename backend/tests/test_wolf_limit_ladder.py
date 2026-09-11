# -*- coding: utf-8 -*-
"""B4 涨停梯队/连板结构（2026-09-11）单测。

狼大原话（XLS 2025-04-21，他的每日复盘流程）：
  「看第10的涨停板方向主要是观察**哪些梯队结构完整**，**哪些集中毕业照**，
    **哪些方向上板失败**，用这些来**判断板块的强弱从而推断出接下来要做的方向**」

口径：本模块是**盘后**判据（limit_list_d 是 EOD 数据，实测盘中返回 0 行）。
数据源实测：该 tushare 代理**忽略 limit_type**（U/Z/D 传什么都返回同一批）→ 必须按 `limit` 列本地过滤。
"""
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_limit_ladder as LL  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    for k in ("WOLF_LIMIT_LADDER", "WOLF_LIMIT_LADDER_FILE", "WOLF_LL_GRAD_MIN_N",
              "WOLF_LL_FAIL_RATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


def _row(code="600000.SH", name="甲", ind="电力", lt="U", times=1, opened=0):
    return {"ts_code": code, "name": name, "industry": ind, "limit": lt,
            "limit_times": times, "open_times": opened, "pct_chg": 10.0}


class TestClassify:
    def test_graduation_when_many_first_boards_no_height(self):
        """集中毕业照 = 家数够多但**最高只有 1 板**（真实样本：2026-08-20 生物制品 14 家全首板）。"""
        assert LL.classify_ladder([], 14, 0, 1, {"1": 14, "2": 0, "3+": 0}) == "graduation"

    def test_failed_when_broken_board_rate_high(self):
        """上板失败 = 炸板率高（2 家涨停 vs 4 家炸板）。"""
        assert LL.classify_ladder([], 2, 4, 1, {"1": 2, "2": 0, "3+": 0}) == "failed"

    def test_failed_takes_priority_over_graduation(self):
        """同时够格时，**上板失败优先**（先说明"上不去"，而非"上去了但没高度"）。"""
        assert LL.classify_ladder([], 6, 6, 1, {"1": 6, "2": 0, "3+": 0}) == "failed"

    def test_not_failed_when_broken_below_one(self):
        """只炸 1 家不算"上板失败"（n_z>=2 门槛）。"""
        assert LL.classify_ladder([], 1, 1, 1, {"1": 1, "2": 0, "3+": 0}) == "plain"

    def test_complete_ladder_with_three_boards(self):
        """梯队结构完整：1板/2板/3+板俱全。"""
        assert LL.classify_ladder([], 3, 0, 3, {"1": 1, "2": 1, "3+": 1}) == "complete"

    def test_complete_ladder_when_two_boards_two_firms(self):
        """1 板 + 2 板 ≥2 家（有承接、有延续）也算完整。"""
        assert LL.classify_ladder([], 4, 0, 2, {"1": 1, "2": 3, "3+": 0}) == "complete"

    def test_gap_ladder_is_not_complete(self):
        """断层（有 1 板、有 3+ 板但**没有 2 板**）不算"结构完整"。"""
        assert LL.classify_ladder([], 3, 0, 5, {"1": 2, "2": 0, "3+": 1}) == "plain"

    def test_plain(self):
        assert LL.classify_ladder([], 1, 0, 1, {"1": 1, "2": 0, "3+": 0}) == "plain"

    def test_thresholds_env_tunable(self, monkeypatch):
        """阈值是**可调参数**（狼大原话未给数字）→ 必须能被 env 覆盖。"""
        rows = {"1": 3, "2": 0, "3+": 0}
        assert LL.classify_ladder([], 3, 0, 1, rows) == "plain"          # 默认 GRAD_MIN_N=4
        monkeypatch.setenv("WOLF_LL_GRAD_MIN_N", "3")
        assert LL.classify_ladder([], 3, 0, 1, rows) == "graduation"
        monkeypatch.setenv("WOLF_LL_FAIL_RATE", "0.1")
        assert LL.classify_ladder([], 8, 2, 1, {"1": 8, "2": 0, "3+": 0}) == "failed"


class TestCoercion:
    def test_nan_safe(self):
        """tushare 的 limit_times 有 NaN → 直接 int() 会崩（首版就在此返回 None）。"""
        assert LL._i(float("nan")) == 0
        assert LL._i(None) == 0
        assert LL._i("") == 0
        assert LL._i("3") == 3
        assert LL._f(float("nan")) == 0.0
        assert LL._f("1.5") == 1.5


class TestSummarize:
    def test_totals_and_industry_ladder(self):
        rows = [_row("600001.SH", "甲", "电力", "U", 1),
                _row("600002.SH", "乙", "电力", "U", 2),
                _row("600003.SH", "丙", "电力", "Z", 1),
                _row("600004.SH", "丁", "银行", "D", 1)]
        r = LL.summarize(rows)
        assert r["totals"] == {"u": 2, "z": 1, "d": 1}
        elec = r["by_industry"]["电力"]
        assert elec["u_n"] == 2 and elec["z_n"] == 1 and elec["max_times"] == 2
        assert elec["ladder"] == {"1": 1, "2": 1, "3+": 0}
        assert elec["names"][0] == "乙"          # 按连板数降序
        # 只有跌停的行业**不进梯队榜**
        assert "银行" not in r["by_industry"]

    def test_by_theme_uses_theme_of_symbol(self, monkeypatch):
        fake = types.ModuleType("wolf_context")
        fake.theme_of_symbol = lambda s: "半导体" if str(s).startswith("688") else None
        monkeypatch.setitem(sys.modules, "wolf_context", fake)
        rows = [_row("688001.SH", "甲", "半导体", "U", 3),
                _row("688002.SH", "乙", "半导体", "U", 1),
                _row("600001.SH", "丙", "电力", "U", 1)]
        r = LL.summarize(rows)
        assert r["by_theme"]["半导体"]["u_n"] == 2
        assert r["by_theme"]["半导体"]["max_times"] == 3
        assert "电力" not in r["by_theme"]        # 映射不到主题的票不进主题榜
        assert "电力" in r["by_industry"]


class TestFetch:
    def test_filters_no_limit_type_and_parses(self, monkeypatch):
        """实测该代理**忽略 limit_type** → 不得依赖它过滤；按 `limit` 列本地过滤。"""
        pd = pytest.importorskip("pandas")

        class _Pro:
            def __init__(self):
                self.kwargs = None

            def limit_list_d(self, **kw):
                self.kwargs = kw
                return pd.DataFrame([
                    {"ts_code": "600001.SH", "name": "甲", "industry": "电力", "limit": "u",
                     "limit_times": 2, "open_times": 1, "pct_chg": 10.0},
                    {"ts_code": "600002.SH", "name": "乙", "industry": "电力", "limit": "Z",
                     "limit_times": float("nan"), "open_times": float("nan"), "pct_chg": -3.0},
                ])

        pro = _Pro()
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro", lambda: pro)
        rows = LL.fetch("20260910")
        assert pro.kwargs == {"trade_date": "20260910"}       # 不传 limit_type
        assert len(rows) == 2
        assert rows[0]["limit"] == "U"                        # 归一化大写
        assert rows[1]["limit_times"] == 0                    # NaN → 0，不崩

    def test_empty_returns_none(self, monkeypatch):
        pd = pytest.importorskip("pandas")
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro",
                            lambda: types.SimpleNamespace(limit_list_d=lambda **kw: pd.DataFrame()))
        assert LL.fetch("20260910") is None

    def test_exception_returns_none(self, monkeypatch):
        def _boom():
            raise RuntimeError("no token")
        monkeypatch.setattr("app.core.trading._api_config.get_tushare_pro", _boom)
        assert LL.fetch("20260910") is None


class TestScanAndSave:
    def test_saves_state(self):
        rows = [_row("600001.SH", "甲", "电力", "U", 1),
                _row("600002.SH", "乙", "电力", "U", 2)]
        r = LL.scan_and_save("20260910", rows=rows)
        assert r["ok"] and r["date"] == "20260910"
        st = LL.load()
        assert st["date"] == "20260910" and st["by_industry"]["电力"]["u_n"] == 2

    def test_fetch_failure_does_not_clobber_state(self, monkeypatch):
        """取数失败**不得覆盖**已有状态文件（fail-safe）。"""
        LL.scan_and_save("20260909", rows=[_row()])
        before = open(LL._path(), encoding="utf-8").read()
        monkeypatch.setattr(LL, "fetch", lambda d: None)
        r = LL.scan_and_save("20260910")
        assert r["ok"] is False and r["reason"] == "fetch_failed"
        assert open(LL._path(), encoding="utf-8").read() == before

    def test_dry_run_no_write(self):
        LL.scan_and_save("20260910", rows=[_row()], dry_run=True)
        assert LL.load() == {}


class TestDirective:
    def test_shows_both_dimensions(self):
        """**两个维度都要给**：主题映射覆盖窄（生产 3/35），不能让它挡掉全市场的行业信号。"""
        LL.scan_and_save("20260910", rows=[
            _row("600001.SH", "甲", "电力", "U", 1),
            _row("600002.SH", "乙", "电力", "U", 2),
            _row("600003.SH", "丙", "化学制药", "U", 1),
            _row("600004.SH", "丁", "化学制药", "U", 1),
            _row("600005.SH", "戊", "化学制药", "U", 1),
            _row("600006.SH", "己", "化学制药", "U", 1)])
        st = LL.load()
        st["by_theme"] = {"医药": st["by_industry"]["化学制药"]}
        import json
        json.dump(st, open(LL._path(), "w", encoding="utf-8"), ensure_ascii=False)
        d = LL.directive()
        assert "2026-09-10" in d
        assert "【主题】" in d and "【行业·异常】" in d       # 主题 + 行业都给
        assert "集中毕业照" in d                             # 4 家全首板 → 异常信号
        assert "医药：4涨停" in d and "化学制药：4涨停" in d
        # 同一行业不得在"异常"和"家数"两行里重复出现（出现两次 = 白占上下文）
        assert d.count("化学制药：4涨停") == 1
        assert "涨停6 炸板0 跌停0" in d

    def test_industry_only_when_no_theme(self):
        LL.scan_and_save("20260910", rows=[
            _row("600001.SH", "甲", "电网设备", "U", 1),
            _row("600002.SH", "乙", "电网设备", "U", 1),
            _row("600003.SH", "丙", "电网设备", "Z", 1),
            _row("600004.SH", "丁", "电网设备", "Z", 1)])
        d = LL.directive()
        assert "【主题】" not in d and "【行业·异常】" in d
        assert "上板失败" in d                                # 2涨停/2炸板 → 失败

    def test_totals_in_header(self):
        LL.scan_and_save("20260910", rows=[_row("600001.SH", "甲", "电力", "U", 1),
                                           _row("600002.SH", "乙", "电力", "Z", 1),
                                           _row("600003.SH", "丙", "电力", "D", 1)])
        assert "涨停1 炸板1 跌停1" in LL.directive()

    def test_empty_state_returns_blank(self):
        assert LL.directive() == ""


class TestWiredIntoContext:
    def test_discipline_context_includes_ladder(self, monkeypatch):
        """接线验证：B4 必须真的进 discipline_context（防"有代码无调用点"的静默失效）。"""
        import app.services.wolf_discipline as WD
        LL.scan_and_save("20260910", rows=[
            _row("600001.SH", "甲", "电力", "U", 1), _row("600002.SH", "乙", "电力", "U", 2)])
        monkeypatch.setattr(WD, "weekend_de_risk", lambda *a, **k: {"active": False, "directive": ""})
        monkeypatch.setattr(WD, "board_half", lambda *a, **k: {"enabled": False, "active_sells": []})
        monkeypatch.setattr(WD, "profit_take", lambda *a, **k: {"enabled": False, "active_sells": []})
        monkeypatch.setattr(WD, "position_cap", lambda *a, **k: {})
        monkeypatch.setattr("app.services.wolf_index_breadth.directive", lambda *a, **k: "")
        monkeypatch.setattr("app.services.wolf_trade_window.directive", lambda *a, **k: "")
        ctx = WD.discipline_context()
        assert "涨停梯队" in ctx and "2026-09-10" in ctx


class TestSwitch:
    def test_enabled_default(self):
        assert LL.enabled() is True

    def test_env_off(self, monkeypatch):
        monkeypatch.setenv("WOLF_LIMIT_LADDER", "0")
        assert LL.enabled() is False

    def test_state_file_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WOLF_LIMIT_LADDER_FILE", "custom_ll.json")
        assert LL._path().endswith("custom_ll.json")
        assert str(tmp_path) in LL._path()

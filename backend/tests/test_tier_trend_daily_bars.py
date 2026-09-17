# -*- coding: utf-8 -*-
"""趋势强度校验的日线取数口径单测（2026-09-17，P1 缺陷 6）。

## 缺陷（修复前）

`position_tier_monitor.check_trend_strength` 取日线写死 `limit=15`（窗口只有 30 天 ≈ 20 个交易日），
但**日线降级分支**（实时 MA5/MA20 取不到时）要 `len(closes) >= 20` 才算得出 MA20：
    `ma20 = float(sum(closes[-20:]) / 20) if len(closes) >= 20 else 0`
⇒ 降级路径下 `ma20` 恒 0 → `ma_align` 恒 `passed=False`，而 `ma_align` 是**核心必过项**
⇒ 一旦实时 MA 不可用，"MA多头排列"永远进 `failed_items`，趋势门控**必失败**（= 加仓被永久拦掉）。

## 修法

① 取数显式取够：窗口 30 天 → 90 天、`limit=15` → `limit=60`（够 MA20/MA60；下游只做
   `closes[-20:]` / `closes[-10:-5]` 等切片，多取不改变任何计算结果）；
② 数据真的不足（源只给 15 根）时**显式登记缺陷**（detail 带根数 + logger.warning），
   而不是把"取数不足"伪装成"趋势不合格"。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.api.indicator as IND  # noqa: E402
import app.config as CFG  # noqa: E402
import app.core.trading._api_config as APIC  # noqa: E402
import app.services.position_tier_monitor as M  # noqa: E402


def _daily_df(n: int, ma5_above: bool = True) -> pd.DataFrame:
    """造 n 根日线：MA5 > MA20（ma5_above）或 MA5 < MA20。"""
    tail, head = (12.0, 10.0) if ma5_above else (10.0, 12.0)
    closes = [head] * (n - 5) + [tail] * 5
    end = datetime.now()
    dates = [(end - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)][::-1]
    return pd.DataFrame({"trade_date": dates, "close": closes, "vol": [1000.0] * n})


class _FakePro:
    def __init__(self, df):
        self._df = df
        self.calls = []

    def daily(self, **kw):
        """模拟 tushare `daily` 的 limit 语义：按交易日倒序返回**最新** limit 根。"""
        self.calls.append(kw)
        n = int(kw.get("limit") or len(self._df))
        return self._df.sort_values("trade_date", ascending=False).head(n).copy()


@pytest.fixture
def monitor():
    mon = M.PositionTierMonitor.__new__(M.PositionTierMonitor)
    mon._trend_cache = {}
    mon._fetch_realtime_ma = lambda symbol: {}          # 实时 MA 取不到 → 强制走日线降级分支
    mon._fetch_sector_flow = lambda symbol: (1e8, "测试板块")
    mon._fetch_moneyflow_today = lambda symbol: 1e8
    mon._get_stock_concept_names = lambda symbol: set()
    return mon


def _run(monitor, df, regime="trend"):
    pro = _FakePro(df)
    with mock.patch.object(APIC, "get_tushare_pro", lambda: pro), \
         mock.patch.object(IND, "_normalize_to_ts_code", lambda s: "300750.SZ"), \
         mock.patch.object(IND, "_get_market_regime_for_calc", lambda: regime), \
         mock.patch.object(CFG, "get_settings",
                           lambda: mock.Mock(get_tushare_token=lambda: "test-token")):
        out = monitor.check_trend_strength("300750")
    return out, pro


# ────────────────────────── 取数口径 ──────────────────────────

def test_daily_fetch_asks_enough_bars_for_ma20(monitor):
    """修复锚点：日线请求必须取够 MA20（≥20 根）——旧代码是 limit=15，必然不够。"""
    out, pro = _run(monitor, _daily_df(30))
    assert pro.calls, "没有调用 pro.daily"
    kw = pro.calls[0]
    assert int(kw.get("limit", 0)) >= 20, f"limit={kw.get('limit')} 不足 20 根，MA20 永远算不出来"
    assert int(kw.get("limit", 0)) == 60
    # 窗口也要够（30 天窗口只有 ~20 个交易日，遇假期就不足 20 根）
    start = datetime.strptime(kw["start_date"], "%Y%m%d")
    end = datetime.strptime(kw["end_date"], "%Y%m%d")
    assert (end - start).days >= 60, f"窗口只有 {(end - start).days} 天"


# ────────────────────────── 降级分支行为 ──────────────────────────

def test_ma_align_computed_in_daily_fallback(monitor):
    """日线 30 根 + MA5 > MA20 → 降级分支能算出 MA20，core 项 ma_align = True。"""
    out, _pro = _run(monitor, _daily_df(30, ma5_above=True))
    assert out["checks"]["ma_align"]["passed"] is True, out["checks"]["ma_align"]
    assert "MA5=" in out["checks"]["ma_align"]["value"]
    assert "ma_align(核心)" not in out["failed_items"]


def test_ma_align_false_only_when_trend_really_bearish(monitor):
    """MA5 < MA20 时才是真 False（而不是"算不出来"）。"""
    out, _pro = _run(monitor, _daily_df(30, ma5_above=False))
    ma = out["checks"]["ma_align"]
    assert ma["passed"] is False
    assert "数据缺陷" not in ma["detail"], ma


@pytest.mark.parametrize("n_bars,expect_passed", [(15, False), (25, True)])
def test_daily_bars_15_vs_25(monitor, n_bars, expect_passed):
    """用户点名的对照：源可给 15 根 vs 25 根。

    修复前 limit=15 → 25 根的源也被截成 15 根 ⇒ MA20 算不出 ⇒ ma_align 恒 False（必失败，加仓永久被拦）；
    修复后 limit=60 → 25 根全拿到 ⇒ MA5>MA20 能正常判 True。
    """
    out, pro = _run(monitor, _daily_df(n_bars, ma5_above=True))
    assert int(pro.calls[0]["limit"]) >= 20
    assert out["checks"]["ma_align"]["passed"] is expect_passed, out["checks"]["ma_align"]


def test_insufficient_bars_registered_as_defect_not_silent_false(monitor):
    """源只给 15 根（旧 limit 的后果）→ 登记"数据缺陷 + 根数"，不再伪装成趋势不合格。"""
    out, _pro = _run(monitor, _daily_df(15))
    ma = out["checks"]["ma_align"]
    assert ma["passed"] is False
    assert "数据缺陷" in ma["detail"] and "15" in ma["detail"], ma
    assert ma["value"] == "N/A"


def test_oscillation_branch_insufficient_bars_registered(monitor):
    """震荡市分支（60 分线不足 → 日线降级）同样要登记根数。"""
    with mock.patch("app.core.trading._60min_analysis.get_60min_ma_values",
                    lambda code: {"ma10": 0, "ma30": 0, "bar_count": 3}):
        out, _pro = _run(monitor, _daily_df(15), regime="oscillation")
    ma = out["checks"]["ma_align"]
    assert ma["passed"] is False and "数据缺陷" in ma["detail"], ma


def test_oscillation_branch_daily_fallback_works(monitor):
    """震荡市 + 60 分线不足 + 日线够 30 根 → 仍能给出 MA5>MA20 结论。"""
    with mock.patch("app.core.trading._60min_analysis.get_60min_ma_values",
                    lambda code: {"ma10": 0, "ma30": 0, "bar_count": 3}):
        out, _pro = _run(monitor, _daily_df(30, ma5_above=True), regime="oscillation")
    ma = out["checks"]["ma_align"]
    assert ma["passed"] is True and "日线降级" in ma["detail"], ma

# -*- coding: utf-8 -*-
"""§9.862 回归测试（2026-10-14 用户「开始修复吧」✓）—— 三个 bug 各一条 ✓：

① `build_position_filter._bars_rows`：排序取价**必须用"覆盖当日"的那个库** ✓
   实测现场 ✗：臂沙箱里的 `mkt_bars_local.db` 只到 20251231（但行数够 ✓）
   ⇒ 旧实现按顺序采用它 ⇒ 05-08 取到"现价"= **27.09**（真实 = 45.16 ✓）
   ⇒ near60 排序 / 乖离分层全部算在 2025 年价格上 ✗。
   注意 ✗：`bars.sqlite` 里没有 `mkt_bars_daily` 表（旧实现的 `except: continue` 会把
   **整个库**跳掉、连 `bars` 表都不试 ✗）—— 所以这条测试同时覆盖"表名回退" ✓。

② `rank_trend_legs`：**已过期腿**（cut 收盘已高于 突破位×(1+chase)）不占 top-k 名额 ✓
   现场 ✗：05-08 东材 SH601208 腿 level=39.14、上限 40.31，05-07 收盘 **45.16**（+15.4%）
   ⇒ 当日不可能按突破价成交 ✗，却占了一个名额 ✗。

③ `wolf_t_rules.defensive_t_reduce_quote`：档位门（build 不触发 ✓）＋ 量比**时段伸缩** ✓
   现场 ✗：05-13 09:35 东材在 **build 档**被"防御减T"卖出（买入后 17 小时 ✗）；
   且规则自认 `vr=0.07`（缩量 ✗）而同一触发 snapshot 的引擎口径量比 = **3.408**（放量 ✓）。
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import build_position_filter as BPF          # noqa: E402
from app.services import wolf_t_rules as WTR                   # noqa: E402


def _mkdb(path, ts_code, rows):
    cn = sqlite3.connect(path)
    cn.execute("create table mkt_bars_daily (ts_code text, trade_date text, close real, high real)")
    cn.executemany("insert into mkt_bars_daily values (?,?,?,?)",
                   [(ts_code, d, c, h) for d, c, h in rows])
    cn.commit()
    cn.close()


def _rows(end_day, base, n=70):
    """造 n 根日线（步长 1 天，日期用 YYYYMMDD 递减 ✓）—— 只看"最新交易日" ✓"""
    from datetime import datetime, timedelta
    d0 = datetime.strptime(end_day, "%Y%m%d")
    return [((d0 - timedelta(days=i)).strftime("%Y%m%d"), base + i * 0.01, base + i * 0.02)
            for i in range(n)]


def test_bars_rows_prefers_db_covering_the_day(tmp_path):
    old = str(tmp_path / "old.db")      # 旧库：只到 2025-12-31（臂沙箱现场 ✓）
    _mkdb(old, "601208.SH", _rows("20251231", 27.0))
    fresh_sqlite = str(tmp_path / "bars.sqlite")   # 新库：只有 bars 表（无 mkt_bars_daily ✓）
    cn = sqlite3.connect(fresh_sqlite)
    cn.execute("create table bars (ts_code text, trade_date text, close real, high real)")
    cn.executemany("insert into bars values (?,?,?,?)",
                   [("601208.SH", d, c, h) for d, c, h in _rows("20260507", 45.0)])
    cn.commit()
    cn.close()

    px, _hi, _g = BPF._feat_from_bars([old, fresh_sqlite], "601208.SH", "20260507")
    assert px == pytest.approx(45.0), "必须采用覆盖 05-07 的那个库（bars 表回退也不能被跳过 ✗）"
    px_old, _h, _g2 = BPF._feat_from_bars([old], "601208.SH", "20260507")
    assert px_old == pytest.approx(27.0), "只有旧库时仍可用（退化路径 ✓）"


def test_rank_excludes_expired_leg(tmp_path):
    day = "20260507"
    _mkdb(str(tmp_path / "mkt_bars_local.db"), "601208.SH", _rows(day, 45.0))
    cn = sqlite3.connect(str(tmp_path / "mkt_bars_local.db"))
    cn.executemany("insert into mkt_bars_daily values (?,?,?,?)",
                   [("002415.SZ", d, c, h) for d, c, h in _rows(day, 35.0)])
    cn.commit()
    cn.close()
    rows = [{"symbol": "SH601208", "level": 39.14},    # 45.0 ≫ 39.14×1.03 ⇒ 过期 ✗
            {"symbol": "SZ002415", "level": 36.29}]    # 35.0 < 36.29 ⇒ 未过期 ✓
    os.environ["WOLF_BREAKOUT_RANK_TOP"] = "1"
    os.environ["WOLF_BREAKOUT_RANK_FACTOR"] = "near60"
    try:
        keep = BPF.rank_trend_legs(list(rows), day, 1, factor="near60", data_dir=str(tmp_path))
    finally:
        os.environ.pop("WOLF_BREAKOUT_RANK_TOP", None)
        os.environ.pop("WOLF_BREAKOUT_RANK_FACTOR", None)
    assert [r["symbol"] for r in keep] == ["SZ002415"], "过期腿不得占 top-k 名额 ✗"


def test_defensive_build_wave_does_not_trigger():
    prev = [{"close": 45.8, "high": 46.9, "low": 44.63, "vol": 923605.26}] * 5
    avg = 923605.26
    q = {"current": 46.25, "high": 47.01, "low": 46.25, "open": 47.01, "vol": avg * 0.07 / 100.0}
    ok, why = WTR.defensive_t_reduce_quote(q, prev, wave_op="build")
    assert ok is False and why == "wave allow build", "build 档不得触发防御减T ✗"


def test_defensive_vol_ratio_is_session_scaled(monkeypatch):
    prev = [{"close": 45.8, "high": 46.9, "low": 44.63, "vol": 923605.26}] * 5
    avg = 923605.26
    q = {"current": 46.25, "high": 47.01, "low": 46.25, "open": 47.01, "vol": avg * 0.07 / 100.0}
    monkeypatch.setattr(WTR, "_session_open_minutes", lambda now=None: 5)     # 09:35
    assert WTR.defensive_t_reduce_quote(q, prev, wave_op="t_only")[0] is False, \
        "09:35 累计量 0.07×全天均量 ⇒ 时段伸缩后是放量 ⇒ 不得判「量能不足」✗"
    monkeypatch.setattr(WTR, "_session_open_minutes", lambda now=None: 240)   # 14:00
    q2 = dict(q, vol=avg * 0.5 / 100.0)                                       # 真缩量 ✓
    assert WTR.defensive_t_reduce_quote(q2, prev, wave_op="t_only")[0] is True, \
        "收盘前真缩量仍应触发（不能把规则改死 ✗）"

# -*- coding: utf-8 -*-
"""`jobs/eval_shadow_v3.py`（影子期对照）定向单测 —— 2026-09-15 round 7。

覆盖：符号格式转换、前瞻窗口未走完记 pending（不当作 0）、同日同主题配对差、
同主题等权篮子（缺 bar 的成分剔除而不是把篮子算成 NaN）、leader 为空/无 bar 的跳过计数、
以及 P1 影子（被拦主题的篮子收益）。

用**合成 parquet 面板**跑真 `Panel`（不打桩），避免"测试自己实现一遍尺子"。
"""
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eval_shadow_v3 as S  # noqa: E402


DAYS = ["20260105", "20260106", "20260107", "20260108", "20260109", "20260112", "20260113"]
# A 每日 +1%、B 每日 −2%、C 每日 +2%（入场 = 影子日次日收盘，出场 = 入场后 hold 日收盘）
# 影子日 0（20260105）+ hold=2 → 入场 01-06 收盘、出场 01-08 收盘，故：
#   A +2.01%、B −3.96%、C +4.04%；篮子(A,B) = −0.975%；篮子(A,B,C) = +0.697%
CLOSES = {"000001.SZ": [10.0, 10.1, 10.201, 10.303, 10.406, 10.510, 10.615],
          "000002.SZ": [10.0, 9.8, 9.604, 9.41192, 9.22368, 9.03921, 8.85842],
          "600000.SH": [10.0, 10.2, 10.404, 10.61208, 10.82432, 11.04081, 11.26162]}


@pytest.fixture()
def panel(tmp_path):
    rows = []
    for ts, closes in CLOSES.items():
        for d, c in zip(DAYS, closes):
            rows.append({"ts_code": ts, "trade_date": d, "open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "pre_close": c, "pct_chg": 0.0, "amount": 1e8,
                         "total_mv": 1e6, "is_st": False})
    p = tmp_path / "bars.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return S.E.Panel(str(p))


def test_xq_to_ts():
    assert S.xq_to_ts("SH603986") == "603986.SH"
    assert S.xq_to_ts("sz000001") == "000001.SZ"
    assert S.xq_to_ts("603986.SH") == "603986.SH"      # 已是 ts_code → 原样
    assert S.xq_to_ts("") is None
    assert S.xq_to_ts("300189") is None                # 无市场前缀，不可识别


def test_paired_diff_and_basket(panel):
    """v3=A(+1%/日) vs leader=B(−1%/日)，篮子=主题成分等权 → 配对差为正，超额口径一致。"""
    rank = [{"date": "20260105", "theme": "T", "mode": "shadow", "qtile": 1.0, "domain_n": 2,
             "v3": ["SZ000001"], "leader": ["SZ000002"]}]
    uni = {"T": ["000001.SZ", "000002.SZ"]}
    res = S.evaluate(rank, [], panel, uni, hold=2)

    r = res["detail"][0]
    # 入场 01-06 收盘 10.1 → 出场 01-08 收盘 10.303 → +2.01%
    assert r["v3"] == pytest.approx(2.01, abs=0.01)
    assert r["leader"] == pytest.approx(-3.96, abs=0.01)      # 9.41192/9.8 − 1
    assert r["basket"] == pytest.approx((2.01 - 3.96) / 2, abs=0.01)
    assert r["v3_ex"] == pytest.approx(r["v3"] - r["basket"], abs=0.01)
    assert res["agg"]["n_paired"] == 1
    assert res["agg"]["paired"]["mean"] == pytest.approx(r["v3"] - r["leader"], abs=0.01)
    assert res["skipped"] == {}


def test_pending_window_excluded(panel):
    """前瞻窗口未走完（01-13 需要 01-15 收盘）→ 记 pending，**不当作 0**、不进聚合。"""
    rank = [{"date": "20260113", "theme": "T", "qtile": None, "domain_n": 2,
             "v3": ["SZ000001"], "leader": []}]
    res = S.evaluate(rank, [], panel, {"T": ["000001.SZ"]}, hold=2)
    assert res["detail"][0]["v3"] is None
    assert res["detail"][0]["reason"] == "pending"
    assert res["agg"]["n_v3"] == 0 and res["agg"]["paired"]["n"] == 0
    assert res["skipped"].get("v3:pending") == 1
    assert res["skipped"].get("leader_empty") == 1


def test_missing_symbol_and_unsorted_date(panel):
    """bar 缺失（020001.SZ 不在面板）→ no_bars；影子日不在 bars（非交易日）→ date_not_in_bars。"""
    rank = [{"date": "20260105", "theme": "T", "qtile": None, "domain_n": 2,
             "v3": ["SZ020001"], "leader": ["SZ000002"]},
            {"date": "20260110", "theme": "T", "qtile": None, "domain_n": 2,
             "v3": ["SZ000001"], "leader": []}]
    res = S.evaluate(rank, [], panel, {"T": ["000001.SZ", "000002.SZ"]}, hold=2)
    assert res["detail"][0]["reason"] == "no_bars"
    assert res["skipped"].get("v3:no_bars") == 1
    assert res["skipped"].get("date_not_in_bars") == 1
    assert len(res["detail"]) == 1


def test_basket_drops_missing_member(panel):
    """主题成分里有面板缺失的票 → 篮子按**可用成分**等权（不把篮子算成 NaN）。"""
    rank = [{"date": "20260105", "theme": "T", "qtile": None, "domain_n": 3,
             "v3": ["SZ000001"], "leader": []}]
    uni = {"T": ["000001.SZ", "000002.SZ", "600000.SH", "020001.SZ"]}
    res = S.evaluate(rank, [], panel, uni, hold=2)
    # 缺失成分被剔除（若没剔除会 KeyError），篮子 = 三只可用成分等权
    assert res["detail"][0]["basket"] == pytest.approx((2.01 - 3.96 + 4.04) / 3, abs=0.01)
    assert res["detail"][0]["v3_ex"] is not None


def test_p1_shadow_blocked_basket(panel):
    """P1 影子：拦掉的主题算篮子收益（拦对 = 篮子后续为负）。"""
    p1 = [{"date": "20260105", "theme": "T", "passed": False, "why": "资金连续5日净流出"},
          {"date": "20260106", "theme": "T", "passed": True, "why": None}]
    res = S.evaluate([], p1, panel, {"T": ["000001.SZ", "000002.SZ"]}, hold=2)
    b = res["p1_detail"]
    assert len(b) == 2 and b[0]["passed"] is False
    assert b[0]["basket"] == pytest.approx((2.01 - 3.96) / 2, abs=0.01)
    assert res["p1_agg"]["n_blocked"] == 1
    assert res["p1_agg"]["blocked_basket"]["n"] == 1
    assert res["p1_agg"]["passed_basket"]["n"] == 1


def test_accepts_ts_code_symbol(panel):
    """影子文件里的 symbol 也可能是 ts_code（两套写法都要能吃）。"""
    rank = [{"date": "20260105", "theme": "T", "qtile": None, "domain_n": 2,
             "v3": ["000001.SZ"], "leader": ["000002.SZ"]}]
    res = S.evaluate(rank, [], panel, {"T": ["000001.SZ"]}, hold=2)
    assert res["detail"][0]["v3"] == pytest.approx(2.01, abs=0.01)


def test_load_gate_blocked_parses_pick_path(tmp_path):
    """`pick_path_*.json` → 主题门记录（gate_blocked = 被挡；legs 的 theme = 放行）。"""
    import json as _json
    (_json.dump({"date": "20260105",
                 "gate_blocked": [{"theme": "半导体/芯片", "why": "结构未确认: stage=suspect"}],
                 "legs": [{"symbol": "SZ000001", "theme": "T"}]},
                open(tmp_path / "pick_path_20260105.json", "w", encoding="utf-8")))
    rows = S.load_gate_blocked(str(tmp_path))
    got = {(r["theme"], r["allowed"]) for r in rows}
    assert ("半导体/芯片", False) in got
    assert ("T", True) in got
    assert len(rows) == 2


def test_gate_section_baskets(panel):
    """A2 主题门：被挡主题的篮子 vs 当日放行主题的篮子（同一把尺子）。"""
    gate = [{"date": "20260105", "theme": "T", "why": "结构未确认", "allowed": False},
            {"date": "20260105", "theme": "U", "why": "当日有布腿", "allowed": True}]
    uni = {"T": ["000001.SZ", "000002.SZ"],     # 篮子 = (2.01 − 3.96)/2 = −0.975
           "U": ["600000.SH"]}                  # 篮子 = +4.04
    res = S.evaluate([], [], panel, uni, hold=2, gate_rows=gate)
    assert res["gate_agg"]["n_blocked"] == 1 and res["gate_agg"]["n_allowed"] == 1
    assert res["gate_agg"]["blocked_basket"]["mean"] == pytest.approx(-0.975, abs=0.02)
    assert res["gate_agg"]["allowed_basket"]["mean"] == pytest.approx(4.04, abs=0.02)


def test_gate_section_pending_excluded(panel):
    """主题门记录的前瞻窗口没走完 → 记 skipped 且不进聚合（不当作 0）。"""
    gate = [{"date": "20260113", "theme": "T", "why": "x", "allowed": False}]
    res = S.evaluate([], [], panel, {"T": ["000001.SZ"]}, hold=2, gate_rows=gate)
    assert res["gate_agg"]["n_checked"] == 1
    assert res["gate_agg"]["n_blocked"] == 0 and res["gate_agg"]["n_pending"] == 1
    assert res["skipped"].get("gate:date_not_in_bars") is None       # 日期在 bars 里，只是窗口没走完
    assert res["gate_detail"][0]["basket"] is None

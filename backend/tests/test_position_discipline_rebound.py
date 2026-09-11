# -*- coding: utf-8 -*-
"""P1-6 去弱留强：rebound_pct 输入形态回归（2026-09-11 生产事故）。

事故：t_monitor._check_position_discipline 传的是 **list**（_prev_daily 丢掉了日期 key），
而 rebound_pct 原先直接 `sorted(prev_days)` → 对 list 会去**比较 dict 本身** →
`TypeError: '<' not supported between instances of 'dict' and 'dict'`，
被 except 吞掉 → **P1-6 在生产中从未真正生效**（静默失效第 8 例：代码在、判据在、从不执行成功）。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "apps" / "main_line", REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import position_discipline as PD  # noqa: E402


D1 = {"close": 10.0, "high": 11.0, "low": 9.0, "vol": 1.0}
D2 = {"close": 10.5, "high": 11.0, "low": 9.5, "vol": 1.0}


class TestReboundInputForms:
    def test_list_does_not_raise(self):
        """原事故就是这个：list 输入必须不抛异常。"""
        assert PD.rebound_pct([D1, D2]) == 16.67

    def test_dict_form_unchanged(self):
        assert PD.rebound_pct({"20260810": D1, "20260811": D2}) == 16.67

    def test_both_forms_agree(self):
        assert PD.rebound_pct([D1, D2]) == PD.rebound_pct({"20260810": D1, "20260811": D2})

    def test_list_order_is_respected(self):
        """list 形态按调用方给出的顺序（时间升序），不重排。"""
        a = PD.rebound_pct([D1, D2])
        b = PD.rebound_pct([D2, D1])
        assert a != b          # 顺序不同 → 末根不同 → 结果不同

    def test_window_applied(self):
        rows = [{"close": 10.0, "high": 11, "low": 10.0, "vol": 1},
                {"close": 10.0, "high": 11, "low": 8.0, "vol": 1},
                {"close": 10.0, "high": 11, "low": 9.9, "vol": 1}]
        # window=2 → 只看最后两根（low 8.0 / 9.9）→ 低点 8.0，最后收盘 10.0
        assert PD.rebound_pct(rows, window=2) == 25.0
        # window 有下限 2（max(2, window)）→ window=1 等价 window=2
        assert PD.rebound_pct(rows, window=1) == 25.0
        # 窗口放到 3 → 低点仍是 8.0
        assert PD.rebound_pct(rows, window=3) == 25.0

    def test_degenerate_inputs(self):
        assert PD.rebound_pct([]) is None
        assert PD.rebound_pct(None) is None
        assert PD.rebound_pct([D1]) is None                    # <2 根
        assert PD.rebound_pct([{"close": 0, "low": 0}]) is None


class TestSelectWeakContract:
    def test_weakest_selected_ascending(self):
        items = [{"symbol": "A", "rebound": 5.0}, {"symbol": "B", "rebound": 1.0},
                 {"symbol": "C", "rebound": 8.0}]
        res = PD.select_weak(items, gap_pct=3.0, min_positions=3, max_sell=1)
        assert [s["symbol"] for s in res["sells"]] == ["B"]     # 卖最弱（反弹最少）
        assert res["spread"] == 7.0

    def test_no_divergence_no_action(self):
        items = [{"symbol": "A", "rebound": 5.0}, {"symbol": "B", "rebound": 4.0},
                 {"symbol": "C", "rebound": 4.5}]
        assert PD.select_weak(items, gap_pct=3.0, min_positions=3)["sells"] == []

    def test_insufficient_positions_no_action(self):
        assert PD.select_weak([{"symbol": "A", "rebound": 1.0}], min_positions=3)["sells"] == []

    def test_none_rebound_excluded(self):
        items = [{"symbol": "A", "rebound": None}, {"symbol": "B", "rebound": 1.0},
                 {"symbol": "C", "rebound": 9.0}]
        assert PD.select_weak(items, gap_pct=3.0, min_positions=3)["sells"] == []

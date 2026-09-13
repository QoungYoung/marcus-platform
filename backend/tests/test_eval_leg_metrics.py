# -*- coding: utf-8 -*-
"""阶段 0「换尺子」单测：腿配对 / 腿型分类 / 三把尺子 / 块状 t。

被测脚本 `jobs/eval_leg_metrics.py`（不进生产运行时，只在本地与生产容器里跑分析）。
这些测试钉住的是**口径**：FIFO 配对、voided 剔除、profit=0 的 flat 标记、
四类腿的判定规则、T+5/±3% 规则模拟的边界，以及块状 t 的分块方式。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "jobs") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "jobs"))

import eval_leg_metrics as M  # noqa: E402


def _row(i, sym, d, px, qty, reason="", acct="t", created=None, voided=0, profit=0.0):
    return {"id": i, "orderid": "X%d" % i, "symbol": sym, "direction": d, "price": px,
            "volume": qty, "amount": px * qty, "profit": profit,
            "created_at": created or ("2026-08-25T09:40:00.000000" if d == "买入" else "2026-08-26T10:00:00.000000"),
            "trade_date": "2026-08-25" if d == "买入" else "2026-08-26",
            "voided": voided, "reason": reason, "account_id": acct}


def test_norm_symbol_and_date():
    assert M.norm_symbol("SH603259") == "603259.SH"
    assert M.norm_symbol("SZ159915") == "159915.SZ"
    assert M.norm_symbol("603259.SH") == "603259.SH"
    assert M._d8s("2026-08-25") == "20260825"
    assert M._d8s("20260825") == "20260825"


def test_fifo_weighted_entry_and_voided_excluded():
    rows = [
        _row(1, "SH600000", "买入", 10.0, 100),
        _row(2, "SH600000", "买入", 12.0, 100, created="2026-08-25T10:00:00.000000"),
        _row(3, "SH600000", "买入", 99.0, 100, voided=1),          # 撤销单不入账本
        _row(4, "SH600000", "卖出", 11.0, 150, profit=150.0),
    ]
    legs, opens, adds, diag = M.build_legs(rows)
    assert len(legs) == 1
    leg = legs[0]
    # 加权成本 = (100*10 + 50*12) / 150 = 10.6667
    assert leg["entry_px"] == pytest.approx((100 * 10.0 + 50 * 12.0) / 150)
    assert leg["matched_qty"] == 150 and leg["unmatched_qty"] == 0
    assert leg["realized_pct"] == pytest.approx((11.0 / (3200.0 / 300) - 1) * 100)
    # 余下 50 股（第二笔买入的一部分）→ 未平腿
    assert sum(o["qty"] for o in opens) == 50


def test_add_lot_detected_when_position_exists():
    rows = [
        _row(1, "SH600000", "买入", 10.0, 100),
        _row(2, "SH600000", "买入", 11.0, 100, created="2026-08-25T11:00:00.000000"),
    ]
    legs, opens, adds, diag = M.build_legs(rows)
    assert len(adds) == 1 and adds[0]["px"] == 11.0 and adds[0]["qty"] == 100


def test_classify_four_types():
    assert M.classify_leg({"account": "t", "buy_reason": "low_buy", "sell_reason": "high_sell"}, 0, False)[0] == "做T低吸"
    # stock 首建且卖完 → 低位埋伏
    assert M.classify_leg({"account": "stock", "buy_reason": "", "sell_reason": "high_sell"}, 0, False)[0] == "低位埋伏"
    # stock 加仓（买入时已有持仓）
    assert M.classify_leg({"account": "stock", "buy_reason": "low_buy", "sell_reason": "high_sell"}, 0, True)[0] == "确认加仓"
    # 卖出后仍有持仓 → 逃顶/减仓
    assert M.classify_leg({"account": "stock", "buy_reason": "", "sell_reason": ""}, 500, False)[0] == "逃顶/减仓"
    # 黄金坑 DCA 单列
    assert M.classify_leg({"account": "golden_pit", "buy_reason": "dca", "sell_reason": ""}, 0, False)[0] == "黄金坑DCA"
    # 卖腿性质
    assert M.classify_leg({"account": "t", "buy_reason": "", "sell_reason": "止损离场（stop_loss）"}, 0, False)[1] == "止损"


class _Bars(object):
    def __init__(self, rows):
        self.rows = rows

    def get(self, sym):
        return self.rows

    def idx(self, sym, d):
        days = [r[0] for r in self.rows]
        return days.index(d) if d in days else None


def _bars(*closes_lows):
    # (date, open, high, low, close, amount)
    return [(("202608%02d" % (10 + i)), c, c, lo, c, 1000.0) for i, (c, lo) in enumerate(closes_lows)]


def test_ruler_hold_and_rules():
    b = _Bars(_bars((10.0, 10.0), (11.0, 10.5), (12.0, 11.5), (13.0, 12.5), (14.0, 13.0), (15.0, 14.0)))
    # 持 T+5 收盘：15.0 / 10.0 - 1 = +50%
    assert M.ruler_hold(b, "X", "20260810", 10.0, 5) == pytest.approx(50.0)
    # +3% 止盈：T+1 收盘 11.0 ≥ 10.3 → +10%
    assert M.ruler_rules(b, "X", "20260810", 10.0, 5, tp=3) == pytest.approx(10.0)
    # −3% 止损：日线 low 12.5 不触发 → 返回 T+5 收盘 +50%
    assert M.ruler_rules(b, "X", "20260810", 10.0, 5, sl=-3) == pytest.approx(50.0)
    b2 = _Bars(_bars((10.0, 10.0), (9.0, 9.6)))
    # low=9.6 ≤ 9.7 → 止损 −3%
    assert M.ruler_rules(b2, "X", "20260810", 10.0, 5, sl=-3) == pytest.approx(-3.0)


def test_snap_idx_handles_non_trading_dates():
    # 账本 trade_date 可能落在周末（实测 2026-08-23 周日 / 08-29 周六）→ 顺延到下一个交易日
    b = _Bars([("20260821", 10, 10, 10, 10, 1), ("20260824", 10, 10, 10, 10, 1), ("20260825", 10, 10, 10, 10, 1)])
    assert M.snap_idx(b, "X", "20260823") == 1
    assert M.snap_idx(b, "X", "20260823", back=True) == 0
    assert M.snap_idx(b, "X", "20260825") == 2
    assert M.snap_idx(b, "X", "20260901") is None


def test_block_t_uses_weekly_blocks():
    pairs = [("20260810", 1.0), ("20260811", 3.0), ("20260817", 2.0), ("20260824", 4.0)]
    rec = M.block_t(pairs, block="W")
    assert rec["blocks"] == 3                      # 08-10/11 同周
    assert rec["mean"] == pytest.approx(round((2.0 + 2.0 + 4.0) / 3, 2))


def test_stat_basic():
    r = M.stat([1.0, -1.0, 2.0])
    assert r["n"] == 3 and r["win"] == pytest.approx(2 / 3, abs=1e-3)
    assert r["median"] == 1.0

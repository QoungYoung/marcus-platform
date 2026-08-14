# -*- coding: utf-8 -*-
"""全行业监测 DCA + 优先级资金池 单测：信号/裁决/窗口推进/配置 seed。"""
import unittest

from app.services.golden_pit_industry_service import (
    INDUSTRY_POOL,
    drawdown_n,
    industry_signal,
    percentile_250,
    ration,
    advance_industry_windows,
)


class TestIndustrySignal(unittest.TestCase):
    def test_dual_condition_in_pit(self):
        ind = INDUSTRY_POOL[0]  # 半导体
        greed = {"2026-01-05": 0.1, "2026-01-06": 0.12}
        px = {d: 1.0 - i * 0.01 for i, d in enumerate(sorted(["2025-11-01", "2025-12-01", "2026-01-06"]))}
        # 60 日回撤不足（高点未跌 20%）
        sig = industry_signal(ind, greed, px, "2026-01-06", 0.15, 0.20, 0.85)
        self.assertFalse(sig["in_pit"])

    def test_greed_lag_uses_latest_available(self):
        """arkvol 序列滞后一天: as_of 无当日贪婪时取最近可用值，不退化纯价格触发。"""
        ind = INDUSTRY_POOL[0]
        greed = {f"2025-12-{d:02d}": 0.1 for d in range(1, 26)}
        greed["2026-01-05"] = 0.05  # 最近可用（序列滞后）
        px = {"2025-11-01": 1.0, "2026-01-06": 0.75}  # 回撤 -25%
        sig = industry_signal(ind, greed, px, "2026-01-06", 0.15, 0.20, 0.85)
        self.assertEqual(sig["greed"], 0.05)
        self.assertIsNotNone(sig["greed_pct"])
        self.assertTrue(sig["in_pit"])

    def test_price_only_when_greed_history_short(self):
        ind = INDUSTRY_POOL[0]
        greed = {}  # 无贪婪历史 -> 仅价格触发
        px = {"2025-11-01": 1.0, "2025-12-01": 1.0, "2026-01-06": 0.75}  # 回撤 -25%
        sig = industry_signal(ind, greed, px, "2026-01-06", 0.15, 0.20, 0.85)
        self.assertTrue(sig["in_pit"])
        self.assertIsNone(sig["greed_pct"])

    def test_overheat_filter(self):
        ind = INDUSTRY_POOL[0]
        # 25 天历史（>=20）贪婪值 0.9 -> 分位 1.0 > entry_cap 0.85 -> 过热过滤
        greed = {f"2025-12-{d:02d}": 0.9 for d in range(1, 26)}
        px = {"2025-11-01": 1.0, "2026-01-06": 0.75}
        sig = industry_signal(ind, greed, px, "2025-12-25", 0.15, 0.20, 0.85)
        self.assertTrue(sig["overheat"])
        self.assertFalse(sig["in_pit"])

    def test_percentile_250(self):
        hist = [0.2] * 200 + [0.05] * 50  # 250 天，0.05 分位 = 50/250 = 0.2
        self.assertAlmostEqual(percentile_250(hist, 0.05), 0.2)
        self.assertEqual(percentile_250(hist, 0.5), 1.0)

    def test_drawdown_n(self):
        px = {"2026-01-01": 1.0, "2026-01-02": 1.0, "2026-01-03": 0.8}
        self.assertAlmostEqual(drawdown_n(px, "2026-01-03"), -0.2)
        self.assertAlmostEqual(drawdown_n(px, "2026-01-01"), 0.0)


class TestRation(unittest.TestCase):
    def test_cash_sufficient_no_cut(self):
        plans = [
            {"id": "a", "priority": 1, "amount": 1000},
            {"id": "b", "priority": 2, "amount": 2000},
        ]
        res = ration(plans, 5000)
        self.assertEqual(len(res["cut_items"]), 0)
        self.assertEqual(res["total_actual"], 3000)

    def test_cash_exhausted_priority_order(self):
        plans = [
            {"id": "low", "priority": 9, "amount": 3000},
            {"id": "high", "priority": 1, "amount": 2000},
        ]
        res = ration(plans, 2500)
        self.assertEqual(res["total_actual"], 2500)
        # 高优先级先全额拿到 2000，低优先级拿到剩余 500
        by_id = {a["id"]: a["actual"] for a in res["allocations"]}
        self.assertEqual(by_id["high"], 2000)
        self.assertEqual(by_id["low"], 500)
        self.assertEqual(len(res["cut_items"]), 0)

    def test_zero_cash_all_cut(self):
        plans = [{"id": "a", "priority": 1, "amount": 1000}]
        res = ration(plans, 0)
        self.assertEqual(res["total_actual"], 0)
        self.assertEqual(res["cut_items"][0]["id"], "a")
        self.assertEqual(res["cut_items"][0]["reason"], "cash_exhausted")


class TestWindowAdvance(unittest.TestCase):
    def test_open_window_and_plan_after_min_days(self):
        cfg = dict(max_total_pct=0.12, cash_min_pct=0.20, pit_pct=0.15, drawdown_pct=0.20,
                   entry_cap=0.85, min_days=2, win_days=15, tp_pct=0.15,
                   time_exit_days=60, stop_loss=0.10)
        pool = [INDUSTRY_POOL[0]]
        iid = pool[0]["id"]
        px = {pool[0]["etf_code"]: {"2026-01-05": 1.0, "2026-01-06": 1.0, "2026-01-07": 1.0, "2026-01-08": 1.0}}
        state = {"windows": {}, "exited": [], "last_as_of": None}

        def sig(day):
            return {iid: {"in_pit": True, "greed_pct": 0.1, "drawdown": -0.3}}

        # 第 1 天: 入坑计数 1，未开窗
        r1 = advance_industry_windows("2026-01-05", sig("2026-01-05"), px, pool, cfg, 250000.0, state)
        self.assertEqual(r1["windows"][iid]["status"], "signal")
        self.assertEqual(r1["plans"], [])
        # 第 2 天: 开窗并首日定投（max_total=30000, dw=0.2 -> 6000）
        r2 = advance_industry_windows("2026-01-06", sig("2026-01-06"), px, pool, cfg, 250000.0, state)
        w = r2["windows"][iid]
        self.assertEqual(w["status"], "accumulating")
        self.assertAlmostEqual(w["invested"], 6000.0, places=1)

    def test_exit_tp_after_window(self):
        cfg = dict(max_total_pct=0.12, cash_min_pct=0.20, pit_pct=0.15, drawdown_pct=0.20,
                   entry_cap=0.85, min_days=1, win_days=3, tp_pct=0.15,
                   time_exit_days=60, stop_loss=0.10)
        pool = [INDUSTRY_POOL[0]]
        iid = pool[0]["id"]
        etf = pool[0]["etf_code"]
        dates = [f"2026-01-{d:02d}" for d in range(1, 9)]
        px = {etf: {d: 1.0 for d in dates}}
        state = {"windows": {}, "exited": [], "last_as_of": None}

        def sig(day):
            return {iid: {"in_pit": True, "greed_pct": 0.1, "drawdown": -0.3}}

        r = None
        for d in dates:
            r = advance_industry_windows(d, sig(d), px, pool, cfg, 250000.0, state)
        # 窗口完成(win_day>=3)后，新一天价格翻倍 -> TP（当日幂等: 同日不再重算）
        px[etf]["2026-01-09"] = 2.0
        r = advance_industry_windows("2026-01-09", sig("2026-01-09"), px, pool, cfg, 250000.0, state)
        self.assertEqual(len(r["exits"]), 1)
        self.assertEqual(r["exits"][0]["reason"], "TP")

    def test_daily_idempotent_replay(self):
        """同一天重复推进 -> 重放今日记录，不重复投资/不推进窗口。"""
        cfg = dict(max_total_pct=0.12, cash_min_pct=0.20, pit_pct=0.15, drawdown_pct=0.20,
                   entry_cap=0.85, min_days=1, win_days=15, tp_pct=0.15,
                   time_exit_days=60, stop_loss=0.10)
        pool = [INDUSTRY_POOL[0]]
        iid = pool[0]["id"]
        etf = pool[0]["etf_code"]
        px = {etf: {"2026-01-05": 1.0, "2026-01-06": 1.0}}
        state = {"windows": {}, "exited": [], "last_as_of": None}

        def sig(day):
            return {iid: {"in_pit": True, "greed_pct": 0.1, "drawdown": -0.3}}

        advance_industry_windows("2026-01-05", sig("2026-01-05"), px, pool, cfg, 250000.0, state)
        r2 = advance_industry_windows("2026-01-05", sig("2026-01-05"), px, pool, cfg, 250000.0, state)
        self.assertTrue(r2.get("replayed"))
        w = state["windows"][iid]
        self.assertEqual(w["invested"], 6000.0)  # 未重复投资
        self.assertEqual(w["win_day"], 1)
        self.assertEqual(state["last_as_of"], "2026-01-05")

    def test_same_day_double_advance_does_not_early_buy(self):
        """同日重复推进（并发/重复调用）不重复累加 pit_days → 第 1 天不提前开窗买入。"""
        cfg = dict(max_total_pct=0.12, cash_min_pct=0.20, pit_pct=0.15, drawdown_pct=0.20,
                   entry_cap=0.85, min_days=2, win_days=15, tp_pct=0.15,
                   time_exit_days=60, stop_loss=0.10)
        pool = [INDUSTRY_POOL[0]]
        iid = pool[0]["id"]
        px = {pool[0]["etf_code"]: {"2026-01-05": 1.0}}
        sig = {iid: {"in_pit": True, "greed_pct": 0.1, "drawdown": -0.3}}
        # 模拟: 早盘某次推进已落盘 pit_days=1 + last_advance=当日，随后执行推进读到同一状态
        state = {"windows": {iid: {"win_start": "2026-01-05", "pit_days": 1, "win_day": 0,
                                   "invested": 0.0, "leftover": 0.0, "qty": 0.0, "cost": 0.0,
                                   "status": "signal", "max_total": 30000, "last_advance": "2026-01-05"}},
                 "exited": [], "last_as_of": None}
        r = advance_industry_windows("2026-01-05", sig, px, pool, cfg, 250000.0, state)
        w = r["windows"][iid]
        self.assertEqual(w["pit_days"], 1)          # 未重复累加
        self.assertEqual(w["status"], "signal")      # 不提前开窗
        self.assertEqual(r["plans"], [])             # 第 1 天无计划

    def test_execute_advances_and_exit_carries_qty(self):
        """execute=True: 推进并返回可下单指令（出场含 etf_code/qty），状态由调用方落盘。"""
        cfg = dict(max_total_pct=0.12, cash_min_pct=0.20, pit_pct=0.15, drawdown_pct=0.20,
                   entry_cap=0.85, min_days=1, win_days=2, tp_pct=0.15,
                   time_exit_days=60, stop_loss=0.10)
        pool = [INDUSTRY_POOL[0]]
        iid = pool[0]["id"]
        etf = pool[0]["etf_code"]
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        px = {etf: {d: 1.0 for d in dates}}
        state = {"windows": {}, "exited": [], "last_as_of": None}

        def sig(day):
            return {iid: {"in_pit": True, "greed_pct": 0.1, "drawdown": -0.3}}

        for d in dates:
            advance_industry_windows(d, sig(d), px, pool, cfg, 250000.0, state, execute=True)
        px[etf]["2026-01-08"] = 2.0
        r = advance_industry_windows("2026-01-08", sig("2026-01-08"), px, pool, cfg, 250000.0, state, execute=True)
        self.assertEqual(len(r["exits"]), 1)
        self.assertEqual(r["exits"][0]["etf_code"], etf)
        self.assertGreater(r["exits"][0]["qty"], 0)
        self.assertFalse(r.get("replayed"))


if __name__ == "__main__":
    unittest.main()

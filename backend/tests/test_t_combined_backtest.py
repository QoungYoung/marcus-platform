# -*- coding: utf-8 -*-
"""做T组合回测测试（add-t-combined-backtest tasks 6.1）。

覆盖：组合编排（建仓→做T→汇总）、建仓规则防前视（as_of 截止）、资金分配（≤净值×55%）、
组合权益汇总（不重复计资金）、报告结构、固定底仓模式、t_build 参数化行为不变。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _make_day(day: str, base: float, drift: float, vol_base: float = 1000.0):
    bars = []
    t = datetime.strptime(day + " 09:30", "%Y%m%d %H:%M")
    price = base
    for i in range(48):
        if i == 24:
            t = datetime.strptime(day + " 13:00", "%Y%m%d %H:%M")
        wave = ((i % 8) - 4) * 0.01
        price = base + drift * (i / 48) + wave
        bars.append({"time": t.strftime("%Y-%m-%d %H:%M:%S"), "open": round(price - 0.005, 3),
                     "close": round(price, 3), "high": round(price + 0.01, 3),
                     "low": round(price - 0.01, 3), "vol": vol_base + (i % 5) * 100,
                     "amount": round(price * (vol_base + (i % 5) * 100), 2)})
        t += timedelta(minutes=5)
    return bars


def _make_fixture(symbols=("AAA", "BBB", "CCC"), days=("20260810", "20260811", "20260812")):
    """落假数据缓存：3 标的 m5 + 40 天日线（可T振幅）+ 指数日线。"""
    tmp = Path(tempfile.mkdtemp(prefix="tbt_comb_test_"))
    (tmp / "m5").mkdir(parents=True, exist_ok=True)
    (tmp / "index_daily").mkdir(parents=True, exist_ok=True)
    (tmp / "stock_daily").mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        m5 = _make_day(days[0], 10.0, -0.2) + _make_day(days[1], 9.9, 0.1) + _make_day(days[2], 10.0, 0.1)
        (tmp / "m5" / f"{sym}.json").write_text(json.dumps(
            {d: [b for b in m5 if str(b["time"])[:10].replace("-", "") == d] for d in days}), encoding="utf-8")
        daily = []
        base_d = datetime(2026, 6, 20)
        for i in range(40):
            d = (base_d + timedelta(days=i)).strftime("%Y%m%d")
            px = 9.5 + i * 0.02
            daily.append({"trade_date": d, "open": round(px - 0.01, 2), "close": round(px, 2),
                          "high": round(px + 0.25, 2), "low": round(px - 0.25, 2),
                          "vol": 1e6, "amount": 1e9})
        # 追加 3 天"未来"日期（晚于回测窗口），验证 as_of 截断
        for i, fd in enumerate(("20260811", "20260812", "20260813")):
            px = 10.0 + i * 0.05
            daily.append({"trade_date": fd, "open": round(px, 2), "close": round(px, 2),
                          "high": round(px + 0.25, 2), "low": round(px - 0.25, 2),
                          "vol": 1e6, "amount": 1e9})
        (tmp / "stock_daily" / f"{sym}.json").write_text(json.dumps(daily), encoding="utf-8")
    idx = [{"trade_date": d, "open": 3000.0, "close": 3000.0, "high": 3010.0, "low": 2990.0, "vol": 1e6} for d in days]
    for ts in ("000300.SH", "000001.SH", "399001.SZ"):
        (tmp / "index_daily" / f"{ts}.json").write_text(json.dumps(idx), encoding="utf-8")
    return tmp


class TestCombinedEngine(unittest.TestCase):
    def setUp(self):
        self.cache = _make_fixture()
        self.symbols = ("AAA", "BBB", "CCC")

    def _task(self, build_mode=True, net_asset=200000.0, limit_ratio=0.55, conditions=None):
        return {
            "symbols": list(self.symbols), "start_date": "2026-08-10", "end_date": "2026-08-12",
            "build_mode": build_mode, "net_asset": net_asset, "build_limit_ratio": limit_ratio,
            "conditions": conditions or [], "review_mode": "rule",
        }

    def test_build_mode_on_builds_and_runs_t(self):
        from app.services.t_backtest import TCombinedBacktestEngine
        r = TCombinedBacktestEngine(self._task(), str(self.cache)).run()
        self.assertEqual(r["status"], "completed")
        built = [d for d in r["build_decisions"] if d["decision"] == "built"]
        self.assertGreaterEqual(len(built), 1, "可T达标标的应按规则建仓")
        self.assertEqual(len(r["per_symbol"]), len(built), "做T阶段标的数 = 建仓数")
        self.assertTrue(all(d["shares"] >= 100 for d in built), "建仓股数 ≥ 100")
        self.assertTrue(len(r["equity_curve"]) > 0, "组合权益曲线非空")
        p = r["portfolio"]
        self.assertEqual(p["initial_asset"], 200000.0, "组合初始净值 20 万")
        # 组合权益首日 ≈ 净值（不重复计资金）
        first_equity = r["equity_curve"][0]["total_asset"]
        self.assertLess(abs(first_equity - 200000.0) / 200000.0, 0.05,
                        f"首日组合权益应≈净值（实际 {first_equity}）")

    def test_build_limit_ratio_caps_allocation(self):
        from app.services.t_backtest import TCombinedBacktestEngine
        # 净值 5 万 + 上限 50%：建仓资金 ≤ 2.5 万；单标的单笔 ≤ 5%（2500 元 → ~200 股）
        r = TCombinedBacktestEngine(self._task(net_asset=50000.0, limit_ratio=0.5), str(self.cache)).run()
        built = [d for d in r["build_decisions"] if d["decision"] == "built"]
        total = sum(d.get("price", 0) * d.get("shares", 0) for d in built)
        self.assertLessEqual(total, 50000.0 * 0.5 + 1, f"建仓总市值 ≤ 净值×50%（实际 {total}）")

    def test_build_mode_off_all_fixed(self):
        from app.services.t_backtest import TCombinedBacktestEngine
        r = TCombinedBacktestEngine(self._task(build_mode=False), str(self.cache)).run()
        self.assertEqual(len(r["per_symbol"]), 3, "固定底仓模式 3 标的全部参与")
        for d in r["build_decisions"]:
            self.assertEqual(d["decision"], "fixed_hold")

    def test_no_lookahead_build_score(self):
        """建仓规则防前视：build_score 用 as_of 截止日线（不含未来交易日）。"""
        from app.services.t_backtest_data import load_stock_daily
        bars = load_stock_daily("AAA", self.cache, as_of="20260810")
        for b in bars:
            self.assertLessEqual(b["trade_date"], "20260810", "as_of 截止数据不含未来")
        # 不带 as_of 时返回全部
        all_bars = load_stock_daily("AAA", self.cache)
        self.assertGreater(len(all_bars), len(bars), "as_of 应截断日线")

    def test_combined_report_structure(self):
        from app.services.t_backtest import TCombinedBacktestEngine
        r = TCombinedBacktestEngine(self._task(), str(self.cache)).run()
        self.assertIn("portfolio", r)
        self.assertIn("build_decisions", r)
        self.assertIn("per_symbol", r)
        self.assertIn("equity_curve", r)
        p = r["portfolio"]
        for k in ("built_count", "total_return_pct", "trigger_count", "executed_count",
                  "win_rate_pct", "max_drawdown_pct", "per_symbol_return"):
            self.assertIn(k, p, f"组合报告缺指标 {k}")
        self.assertTrue(any("建仓口径" in n for n in r["caliber_notes"]), "口径声明含建仓说明")


class TestTBuildParametric(unittest.TestCase):
    def test_build_sizing_injection(self):
        """build_sizing 注入参数生效，生产默认路径兼容。"""
        from app.services.t_build import build_sizing
        r = build_sizing("AAA", 10.0, net_asset=200000.0, total_floor_value=0.0,
                         symbol_value=0.0, regime="ACTIVE")
        self.assertIn("pass", r)
        self.assertGreaterEqual(r["suggest_volume"], 0)
        self.assertEqual(r["net_asset"], 200000.0, "注入净值生效")

    def test_build_score_as_of_and_quality_override(self):
        """build_score as_of + quality_override：日线注入零网络，规则走通。"""
        from app.services.t_build import _quality_from_daily, build_score
        daily = []
        base_d = datetime(2026, 6, 1)
        for i in range(40):
            d = (base_d + timedelta(days=i)).strftime("%Y-%m-%d")
            px = 9.5 + i * 0.02
            daily.append({"date": d, "open": round(px - 0.01, 2), "close": round(px, 2),
                          "high": round(px + 0.25, 2), "low": round(px - 0.25, 2),
                          "vol": 1e6, "amount": 1e9})
        quality = _quality_from_daily(daily)
        self.assertGreaterEqual(quality["score"], 0.5, "可T振幅/流动性达标")
        r = build_score("AAA", source="pool", as_of="2026-08-10",
                        quality_override=quality, bars=daily)
        self.assertIn("score", r)
        self.assertIn("pass_gate", r)
        self.assertIn("trend", r)


if __name__ == "__main__":
    unittest.main()

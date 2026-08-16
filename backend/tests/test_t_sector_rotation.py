# -*- coding: utf-8 -*-
"""板块轮动增强单元测试（add-sector-rotation，覆盖 spec 核心场景）。"""
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path("/app")
for _d in [str(REPO_ROOT / "backend"), "/app"]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from app.services.t_backtest_data import (
    _SW_STRENGTH_SIGMA,
    industry_5d_pct,
    industry_context_for,
    industry_strength_from_pct,
    load_industry_map,
)


def _write_industry_cache(cache_dir: Path, dates: list, name: str = "电子", base: float = 100.0):
    """构造 industry_daily/{date}.json 缓存：closes 依次为 base + i。"""
    for i, d in enumerate(dates):
        out = cache_dir / "industry_daily" / f"{d}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            name: {"index_code": "801080.SI", "pct_change": 1.0, "close": base + i},
        }), encoding="utf-8")


class TestIndustryStrength(unittest.TestCase):
    def test_strength_zero_is_neutral(self):
        self.assertAlmostEqual(industry_strength_from_pct(0.0), 0.5, places=4)

    def test_strength_positive_above_half(self):
        self.assertGreater(industry_strength_from_pct(5.0), 0.9)
        self.assertGreater(industry_strength_from_pct(1.0), 0.5)

    def test_strength_negative_below_half(self):
        self.assertLess(industry_strength_from_pct(-5.0), 0.1)
        self.assertLess(industry_strength_from_pct(-1.0), 0.5)

    def test_strength_none_passthrough(self):
        self.assertIsNone(industry_strength_from_pct(None))

    def test_strength_monotonic(self):
        a = industry_strength_from_pct(-3.0)
        b = industry_strength_from_pct(0.0)
        cc = industry_strength_from_pct(3.0)
        self.assertLess(a, b)
        self.assertLess(b, cc)

    def test_sigma_sane(self):
        # sigma=1.0：±5% 累计涨幅应显著区分（避免 0.04 的过早饱和）
        self.assertAlmostEqual(_SW_STRENGTH_SIGMA, 1.0, places=4)
        self.assertGreater(industry_strength_from_pct(5.0) - industry_strength_from_pct(0.0), 0.4)


class TestIndustry5dPct(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="bt_sector_test_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_5d_returns_cumulative(self):
        dates = ["20260804", "20260805", "20260806", "20260807", "20260810",
                 "20260811", "20260812", "20260813"]
        _write_industry_cache(self.tmp, dates)  # close: 100..107
        # as_of=20260814：只用 < 14 的最近 6 条 = 100..105（close 101..105? 见下）
        # 实际 closes: 100,101,...,107；最近6条 = 102,103,104,105,106,107
        # 5日涨幅 = 107/102 - 1
        pct = industry_5d_pct("电子", "20260814", self.tmp)
        self.assertIsNotNone(pct)
        self.assertAlmostEqual(pct, (107.0 / 102.0 - 1.0) * 100.0, places=3)

    def test_5d_excludes_same_day(self):
        dates = ["20260804", "20260805", "20260806", "20260807", "20260810",
                 "20260811", "20260812", "20260813", "20260814"]
        _write_industry_cache(self.tmp, dates)  # 含 14 当日
        pct = industry_5d_pct("电子", "20260814", self.tmp)
        self.assertAlmostEqual(pct, (107.0 / 102.0 - 1.0) * 100.0, places=3)  # 当日不算（防前视）

    def test_5d_insufficient_data_none(self):
        dates = ["20260810", "20260811", "20260812"]
        _write_industry_cache(self.tmp, dates)
        self.assertIsNone(industry_5d_pct("电子", "20260814", self.tmp))

    def test_missing_industry_none(self):
        _write_industry_cache(self.tmp, ["20260810"])
        self.assertIsNone(industry_5d_pct("不存在的行业", "20260814", self.tmp))


class TestIndustryContext(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="bt_sector_map_"))
        _write_industry_cache(self.tmp,
                              ["20260804", "20260805", "20260806", "20260807", "20260810",
                               "20260811", "20260812", "20260813"],
                              name="电子")
        (self.tmp / "industry_map.json").write_text(json.dumps({
            "300308.SZ": {"l1_code": "801080.SI", "l1_name": "电子"},
        }), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_context_resolved(self):
        ctx = industry_context_for("300308", "20260814", self.tmp)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["name"], "电子")
        self.assertAlmostEqual(ctx["pct_5d"], (107.0 / 102.0 - 1.0) * 100.0, places=3)
        self.assertGreater(ctx["strength"], 0.5)

    def test_context_missing_symbol_none(self):
        self.assertIsNone(industry_context_for("600000", "20260814", self.tmp))

    def test_map_cache_used(self):
        mapping = load_industry_map(self.tmp)
        ctx = industry_context_for("300308", "20260814", self.tmp, map_cache=mapping)
        self.assertIsNotNone(ctx)


class TestBuildScoreIndustryMerge(unittest.TestCase):
    """行业因子并入 build_score：权重 0/1 退化 + 单调。"""
    def _score(self, industry=None, weight=0.3):
        import app.services.t_build as tb
        from unittest import mock
        bars = [{"date": f"2026-07-{d:02d}", "open": 10.0, "close": 10.0 + i * 0.1,
                 "high": 10.0 + i * 0.1 + 0.2, "low": 10.0 + i * 0.1 - 0.2,
                 "vol": 1e6, "amount": 1e7}
                for i, d in enumerate(range(1, 31))]
        quality = {"score": 0.8, "pass_gate": True, "reasons": ["测试"]}
        base_params = dict(tb.BUILD_PARAMS_DEFAULT)
        base_params["industry_strength_weight"] = weight
        with mock.patch.object(tb, "_params", return_value=base_params):
            r = tb.build_score("300308", source="scan", as_of="2026-08-14",
                               quality_override=quality, bars=bars, relax=True,
                               industry=industry)
        return r

    def test_weight_zero_ignores_industry(self):
        r = self._score(industry={"name": "电子", "strength": 0.9, "pct_5d": 5.0}, weight=0.0)
        r0 = self._score(industry=None, weight=0.0)
        self.assertEqual(r["score"], r0["score"])

    def test_weight_one_pure_industry(self):
        r = self._score(industry={"name": "电子", "strength": 0.9, "pct_5d": 5.0}, weight=1.0)
        self.assertAlmostEqual(r["score"], 0.9, places=4)

    def test_strong_industry_beats_weak(self):
        strong = self._score(industry={"name": "电子", "strength": 0.9, "pct_5d": 5.0}, weight=0.3)
        weak = self._score(industry={"name": "银行", "strength": 0.1, "pct_5d": -5.0}, weight=0.3)
        none_ = self._score(industry=None, weight=0.3)
        self.assertGreater(strong["score"], weak["score"])
        self.assertGreater(strong["score"], none_["score"])
        self.assertIn("行业", " ".join(strong["reasons"]))


if __name__ == "__main__":
    unittest.main()
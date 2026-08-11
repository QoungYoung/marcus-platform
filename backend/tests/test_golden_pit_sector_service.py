# -*- coding: utf-8 -*-
"""黄金坑板块拆分选筹服务单元测试：combo 打分、TOP N 选取、权重归一化、
单板块上限截断、空组合跳过、guide_only 不下宽基单。"""
import unittest
from unittest import mock

from app.services.golden_pit_config import (
    CHINA_INDICES,
    GOLDEN_PIT_SECTOR_SPLIT_ENABLED,
    SECTOR_ETF_POOL,
    TECH_SECTOR_POOL,
)
from app.services import golden_pit_sector_service as svc


class TestSectorSplitConfig(unittest.TestCase):
    def test_588000_159915_guide_only(self):
        self.assertTrue(CHINA_INDICES["588000"]["guide_only"])
        self.assertTrue(CHINA_INDICES["159915"]["guide_only"])

    def test_gray_switch_default_off(self):
        self.assertFalse(GOLDEN_PIT_SECTOR_SPLIT_ENABLED)

    def test_pool_has_10_validated_sectors(self):
        self.assertEqual(len(SECTOR_ETF_POOL), 10)
        for key, entry in SECTOR_ETF_POOL.items():
            self.assertIn(entry["etf_code"][:2], ("SH", "SZ"))
            self.assertTrue(entry.get("flow_name"))
            self.assertTrue(entry.get("greed_code"), f"{key} 缺少 greed_code")

    def test_tech7_pool_has_7_validated_sectors(self):
        self.assertEqual(len(TECH_SECTOR_POOL), 7)
        for key, entry in TECH_SECTOR_POOL.items():
            code = entry["etf_code"]
            self.assertIn(code[:2], ("SH", "SZ"))
            self.assertEqual(len(code[2:]), 6)
            self.assertTrue(entry["name"])

    def test_tech7_pool_codes_are_in_tech_hardware_series(self):
        # tech-hardware-greed/series 覆盖的场内代码（剔除 159227/588080 后）
        self.assertEqual(
            {e["etf_code"][2:] for e in TECH_SECTOR_POOL.values()},
            {"159949", "512480", "512930", "515050", "515400", "515880", "588200"},
        )

    def test_pool_excludes_broad_codes(self):
        etf_codes = {e["etf_code"] for e in SECTOR_ETF_POOL.values()}
        self.assertNotIn("SH588000", etf_codes)
        self.assertNotIn("SZ159915", etf_codes)


class TestRankCombo(unittest.TestCase):
    def test_combo_prefers_strong_inflow_and_deep_oversold(self):
        valid = [
            {"sector": "A", "mf5_norm": 5.0, "oversold120": -0.30},
            {"sector": "B", "mf5_norm": 2.0, "oversold120": -0.05},
            {"sector": "C", "mf5_norm": 1.0, "oversold120": -0.10},
        ]
        ranked = svc._rank_combo(valid)
        ranked.sort(key=lambda x: x["combo"], reverse=True)
        self.assertEqual(ranked[0]["sector"], "A")


class TestNormalizeWeights(unittest.TestCase):
    def test_weights_sum_to_one_and_cap(self):
        selected = [
            {"sector": "A", "combo": -3.0},
            {"sector": "B", "combo": -4.0},
            {"sector": "C", "combo": -6.0},
        ]
        out = svc._normalize_weights(selected, 0.5)
        self.assertAlmostEqual(sum(s["weight"] for s in out), 1.0, places=4)
        self.assertTrue(all(s["weight"] <= 0.5 + 1e-9 for s in out))

    def test_equal_scores_split_evenly(self):
        selected = [{"sector": s, "combo": -2.0} for s in ("A", "B", "C")]
        out = svc._normalize_weights(selected, 0.5)
        self.assertAlmostEqual(sum(s["weight"] for s in out), 1.0, places=4)
        self.assertTrue(all(abs(s["weight"] - 1.0 / 3) < 1e-4 for s in out))

    def test_single_sector_full_weight(self):
        out = svc._normalize_weights([{"sector": "A", "combo": -1.0}], 0.5)
        self.assertAlmostEqual(out[0]["weight"], 1.0, places=4)


class TestComputeSignal(unittest.TestCase):
    @staticmethod
    def _flow_df():
        import pandas as pd
        rows = []
        for d in range(25):
            for i, (name, net) in enumerate([("半导体", 100.0), ("通信设备", 10.0), ("计算机", -50.0)]):
                rows.append((pd.Timestamp("2026-05-01") + pd.Timedelta(days=d), f"BK{i}", name, net))
        idx = pd.MultiIndex.from_tuples(
            [(r[0], r[1]) for r in rows], names=["trade_date", "ts_code"]
        )
        return pd.DataFrame(
            {"content_type": "行业", "name": [r[2] for r in rows], "net_amount": [r[3] for r in rows]},
            index=idx,
        )

    @staticmethod
    def _kline(closes):
        return [{"date": f"2026-06-{i % 28 + 1:02d}", "close": c} for i, c in enumerate(closes)]

    def test_invalid_when_flow_history_insufficient(self):
        import pandas as pd
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2026-05-20") + pd.Timedelta(days=d), "BK1") for d in range(5)],
            names=["trade_date", "ts_code"],
        )
        df = pd.DataFrame(
            {"content_type": "行业", "name": ["半导体"] * 5, "net_amount": [100.0] * 5}, index=idx
        )
        with mock.patch.object(svc, "_load_industry_flow_df", return_value=df), \
             mock.patch.object(svc, "_fetch_etf_kline", return_value=self._kline([1.0] * 200)):
            self.assertIsNone(svc._compute_signal("半导体", SECTOR_ETF_POOL["半导体"], df, "2026-06-01"))

    def test_invalid_when_kline_insufficient(self):
        df = self._flow_df()
        with mock.patch.object(svc, "_load_industry_flow_df", return_value=df), \
             mock.patch.object(svc, "_fetch_etf_kline", return_value=self._kline([1.0] * 50)):
            self.assertIsNone(svc._compute_signal("半导体", SECTOR_ETF_POOL["半导体"], df, "2026-06-01"))

    def test_valid_signal_fields(self):
        df = self._flow_df()
        closes = [1.0] * 150 + [0.5] * 50  # 深超跌
        with mock.patch.object(svc, "_load_industry_flow_df", return_value=df), \
             mock.patch.object(svc, "_fetch_etf_kline", return_value=self._kline(closes)):
            sig = svc._compute_signal("半导体", SECTOR_ETF_POOL["半导体"], df, "2026-06-01")
            self.assertIsNotNone(sig)
            self.assertGreater(sig["mf5_norm"], 0)
            self.assertLess(sig["oversold120"], 0)


class TestSelectSectors(unittest.TestCase):
    def setUp(self):
        svc._cache.clear()  # 避免 mock 池在 900s TTL 缓存内串扰
        # 固定 moneyflow 模式，确保旧路径测试不受 DB signal_mode=greed 影响
        self._cfg_patch = mock.patch.object(
            svc, "get_sector_config", return_value={"signal_mode": "moneyflow"}
        )
        self._cfg_patch.start()
        self.addCleanup(self._cfg_patch.stop)
        self.signals = {
            "A": {"sector": "A", "name": "A", "etf_code": "SH111111", "mf5_norm": 5.0, "oversold120": -0.30},
            "B": {"sector": "B", "name": "B", "etf_code": "SH222222", "mf5_norm": 2.0, "oversold120": -0.10},
            "C": {"sector": "C", "name": "C", "etf_code": "SH333333", "mf5_norm": -1.0, "oversold120": -0.05},
            "D": {"sector": "D", "name": "D", "etf_code": "SH444444", "mf5_norm": 3.0, "oversold120": -0.01},
            "E": {"sector": "E", "name": "E", "etf_code": "SH555555", "mf5_norm": 1.5, "oversold120": -0.20},
            "F": {"sector": "F", "name": "F", "etf_code": "SH666666", "mf5_norm": 1.2, "oversold120": -0.15},
        }
        self.pool = {
            k: {"name": v["name"], "etf_code": v["etf_code"], "flow_name": k}
            for k, v in self.signals.items()
        }

    def test_top_n_selection_and_weights(self):
        def fake_compute(pool_key, entry, flow_df, as_of, cfg=None):
            return self.signals.get(pool_key)

        with mock.patch.object(svc, "SECTOR_ETF_POOL", self.pool), \
             mock.patch.object(svc, "SECTOR_MIN_VALID", 4), \
             mock.patch.object(svc, "_compute_signal", side_effect=fake_compute):
            res = svc.select_sectors(as_of="2026-06-01", enabled=True)
        self.assertEqual(len(res["selected"]), 2)
        self.assertEqual(res["selected"][0]["sector"], "A")
        self.assertAlmostEqual(sum(s["weight"] for s in res["selected"]), 1.0, places=4)
        self.assertTrue(all(s["weight"] <= 0.5 + 1e-9 for s in res["selected"]))

    def test_empty_when_too_few_valid(self):
        def fake_compute(pool_key, entry, flow_df, as_of, cfg=None):
            return self.signals.get(pool_key) if pool_key in ("A", "B") else None

        with mock.patch.object(svc, "SECTOR_ETF_POOL", self.pool), \
             mock.patch.object(svc, "SECTOR_MIN_VALID", 4), \
             mock.patch.object(svc, "_compute_signal", side_effect=fake_compute):
            res = svc.select_sectors(as_of="2026-06-01")
        self.assertEqual(res["selected"], [])
        self.assertIn("空仓", res["empty_reason"])

    def test_empty_when_pool_missing(self):
        with mock.patch.object(svc, "SECTOR_ETF_POOL", {}):
            res = svc.select_sectors(as_of="2026-06-01")
        self.assertEqual(res["selected"], [])
        self.assertIn("未配置", res["empty_reason"])




class TestBuildBuyLegs(unittest.TestCase):
    """灰度开关验证: 关闭时回滚宽基直接买入路径；开启时 guide_only 只买板块 ETF。"""

    def test_guide_only_disabled_keeps_broad_path(self):
        from app.services import golden_pit_dca_service as dca
        # 灰度关闭 → guide_only 宽基仍走 90/5/5 宽基买入(回滚路径)；不依赖真实 DB 的 enabled 值
        with mock.patch.object(dca._sector, "get_sector_config", return_value={"enabled": False}):
            legs, notes, reason = dca._build_buy_legs("588000", [], 10000.0, "2026-06-01", "SH588000")
        self.assertEqual(reason, "")
        self.assertEqual(legs[0][0], "index")
        self.assertAlmostEqual(legs[0][2], 10000.0)  # 90% + 5% + 5% 回退 → 100%
        self.assertEqual(len(legs), 1)

    def test_guide_only_enabled_uses_sector_legs(self):
        from app.services import golden_pit_dca_service as dca
        sel = {
            "selected": [
                {"sector": "半导体", "etf_code": "SH512480", "weight": 0.7},
                {"sector": "通信设备", "etf_code": "SH515880", "weight": 0.3},
            ]
        }
        with mock.patch.object(dca, "_sector") as m_sector:
            m_sector.get_sector_config.return_value = {"enabled": True}
            m_sector.select_sectors.return_value = sel
            legs, notes, reason = dca._build_buy_legs("588000", [], 10000.0, "2026-06-01", "SH588000")
        self.assertEqual(reason, "")
        self.assertEqual(len(legs), 2)
        self.assertEqual(legs[0][0], "半导体")  # 宽基本身不下单
        self.assertAlmostEqual(legs[0][2], 7000.0)
        self.assertAlmostEqual(legs[1][2], 3000.0)

    def test_guide_only_enabled_empty_skips(self):
        from app.services import golden_pit_dca_service as dca
        sel = {"selected": [], "empty_reason": "有效信号板块数 1 < 4，空仓等待板块信号"}
        with mock.patch.object(dca, "_sector") as m_sector:
            m_sector.get_sector_config.return_value = {"enabled": True}
            m_sector.select_sectors.return_value = sel
            legs, notes, reason = dca._build_buy_legs("588000", [], 10000.0, "2026-06-01", "SH588000")
        self.assertEqual(legs, [])
        self.assertIn("空仓", reason)

    def test_non_guide_only_keeps_90_5_5(self):
        from app.services import golden_pit_dca_service as dca
        legs, notes, reason = dca._build_buy_legs("510500", [], 10000.0, "2026-06-01", "SH510500")
        self.assertEqual(reason, "")
        self.assertAlmostEqual(legs[0][2], 10000.0)  # 增强未入坑回退 → 全额宽基


class TestComputeSignalGreed(unittest.TestCase):
    """greed 模式单板块信号: 超跌中且当日贪婪可查。"""

    @staticmethod
    def _kline(closes):
        return [{"date": f"2026-06-{i % 28 + 1:02d}", "close": c} for i, c in enumerate(closes)]

    def test_valid_when_oversold_and_greed_available(self):
        closes = [1.0] * 150 + [0.5] * 50  # 深超跌
        greed_map = {"512480": {"2026-06-01": 0.42}}
        with mock.patch.object(svc, "_fetch_etf_kline", return_value=self._kline(closes)):
            sig = svc._compute_signal_greed(
                "半导体", SECTOR_ETF_POOL["半导体"], greed_map, "2026-06-01"
            )
        self.assertIsNotNone(sig)
        self.assertLess(sig["oversold120"], 0)
        self.assertEqual(sig["greed"], 0.42)

    def test_invalid_when_greed_missing(self):
        closes = [1.0] * 150 + [0.5] * 50
        greed_map = {"512480": {"2026-05-31": 0.42}}
        with mock.patch.object(svc, "_fetch_etf_kline", return_value=self._kline(closes)):
            sig = svc._compute_signal_greed(
                "半导体", SECTOR_ETF_POOL["半导体"], greed_map, "2026-06-01"
            )
        self.assertIsNone(sig)

    def test_invalid_when_not_oversold(self):
        closes = [0.5] * 50 + [1.0] * 150  # 上涨中
        greed_map = {"512480": {"2026-06-01": 0.42}}
        with mock.patch.object(svc, "_fetch_etf_kline", return_value=self._kline(closes)):
            sig = svc._compute_signal_greed(
                "半导体", SECTOR_ETF_POOL["半导体"], greed_map, "2026-06-01"
            )
        self.assertIsNone(sig)


class TestRankComboGreed(unittest.TestCase):
    def test_combo_prefers_low_greed_and_deep_oversold(self):
        valid = [
            {"sector": "A", "greed": 0.10, "oversold120": -0.30},
            {"sector": "B", "greed": 0.50, "oversold120": -0.05},
            {"sector": "C", "greed": 0.30, "oversold120": -0.10},
        ]
        ranked = svc._rank_combo_greed(valid)
        ranked.sort(key=lambda x: x["combo"], reverse=True)
        self.assertEqual(ranked[0]["sector"], "A")  # 最低贪婪 + 最深超跌

    def test_ranks_are_relative(self):
        valid = [
            {"sector": "A", "greed": 0.1, "oversold120": -0.3},
            {"sector": "B", "greed": 0.2, "oversold120": -0.2},
            {"sector": "C", "greed": 0.3, "oversold120": -0.1},
        ]
        ranked = svc._rank_combo_greed(valid)
        by_sector = {s["sector"]: s["combo"] for s in ranked}
        # 最低贪婪 rank=1 + 最深超跌 rank=1 → combo 最大
        self.assertEqual(by_sector["A"], -2)
        self.assertEqual(by_sector["B"], -4)
        self.assertEqual(by_sector["C"], -6)


class TestSelectSectorsGreed(unittest.TestCase):
    def setUp(self):
        svc._cache.clear()
        self._cfg_patch = mock.patch.object(
            svc, "get_sector_config", return_value={"signal_mode": "greed", "pool_source": "prod10"}
        )
        self._cfg_patch.start()
        self.addCleanup(self._cfg_patch.stop)
        self.signals = {
            "A": {"sector": "A", "name": "A", "etf_code": "SH111111", "greed": 0.10, "oversold120": -0.30},
            "B": {"sector": "B", "name": "B", "etf_code": "SH222222", "greed": 0.50, "oversold120": -0.05},
            "C": {"sector": "C", "name": "C", "etf_code": "SH333333", "greed": 0.30, "oversold120": -0.10},
            "D": {"sector": "D", "name": "D", "etf_code": "SH444444", "greed": 0.45, "oversold120": -0.02},
            "E": {"sector": "E", "name": "E", "etf_code": "SH555555", "greed": 0.20, "oversold120": -0.20},
            "F": {"sector": "F", "name": "F", "etf_code": "SH666666", "greed": 0.35, "oversold120": -0.15},
        }
        self.pool = {
            k: {"name": v["name"], "etf_code": v["etf_code"], "greed_code": v["etf_code"][2:], "flow_name": k}
            for k, v in self.signals.items()
        }

    def test_greed_top_n_selection(self):
        def fake_compute(pool_key, entry, greed_map, as_of, cfg=None):
            return self.signals.get(pool_key)

        with mock.patch.object(svc, "SECTOR_ETF_POOL", self.pool),              mock.patch.object(svc, "SECTOR_MIN_VALID", 4),              mock.patch.object(svc, "_load_sector_greed_map", return_value={}),              mock.patch.object(svc, "_compute_signal_greed", side_effect=fake_compute):
            res = svc.select_sectors(as_of="2026-06-01", enabled=True)
        self.assertEqual(res["signal_mode"], "greed")
        self.assertEqual(len(res["selected"]), 2)
        self.assertEqual(res["selected"][0]["sector"], "A")
        self.assertAlmostEqual(sum(s["weight"] for s in res["selected"]), 1.0, places=4)
        self.assertTrue(all(s["weight"] <= 0.5 + 1e-9 for s in res["selected"]))

    def test_greed_empty_when_too_few_valid(self):
        def fake_compute(pool_key, entry, greed_map, as_of, cfg=None):
            return self.signals.get(pool_key) if pool_key in ("A", "B") else None

        with mock.patch.object(svc, "SECTOR_ETF_POOL", self.pool),              mock.patch.object(svc, "SECTOR_MIN_VALID", 4),              mock.patch.object(svc, "_load_sector_greed_map", return_value={}),              mock.patch.object(svc, "_compute_signal_greed", side_effect=fake_compute):
            res = svc.select_sectors(as_of="2026-06-01")
        self.assertEqual(res["selected"], [])
        self.assertIn("空仓", res["empty_reason"])

    def test_greed_skips_moneyflow_load(self):
        with mock.patch.object(svc, "SECTOR_ETF_POOL", self.pool),              mock.patch.object(svc, "SECTOR_MIN_VALID", 4),              mock.patch.object(svc, "_load_sector_greed_map", return_value={}),              mock.patch.object(svc, "_load_industry_flow_df", side_effect=AssertionError("不应加载资金流")),              mock.patch.object(svc, "_compute_signal_greed", side_effect=lambda *a, **k: None):
            res = svc.select_sectors(as_of="2026-06-01")
        self.assertEqual(res["selected"], [])


class TestSelectSectorsTech7(unittest.TestCase):
    """tech7 池选筹: pool_source=tech7 走 TECH_SECTOR_POOL + tech-hardware 贪婪。"""

    def setUp(self):
        svc._cache.clear()
        self.signals = {
            "A": {"sector": "A", "name": "A", "etf_code": "SZ159949", "greed": 0.10, "oversold120": -0.30},
            "B": {"sector": "B", "name": "B", "etf_code": "SH512480", "greed": 0.50, "oversold120": -0.05},
            "C": {"sector": "C", "name": "C", "etf_code": "SH512930", "greed": 0.30, "oversold120": -0.10},
            "D": {"sector": "D", "name": "D", "etf_code": "SH515050", "greed": 0.45, "oversold120": -0.02},
            "E": {"sector": "E", "name": "E", "etf_code": "SH515400", "greed": 0.20, "oversold120": -0.20},
            "F": {"sector": "F", "name": "F", "etf_code": "SH515880", "greed": 0.35, "oversold120": -0.15},
            "G": {"sector": "G", "name": "G", "etf_code": "SH588200", "greed": 0.25, "oversold120": -0.12},
        }
        self.pool = {
            k: {"name": v["name"], "etf_code": v["etf_code"]}
            for k, v in self.signals.items()
        }
        self._cfg_patch = mock.patch.object(
            svc, "get_sector_config", return_value={"signal_mode": "greed", "pool_source": "tech7"}
        )
        self._cfg_patch.start()
        self.addCleanup(self._cfg_patch.stop)

    def test_tech7_uses_tech_pool_and_greed_map(self):
        def fake_compute(pool_key, entry, greed_map, as_of, cfg=None):
            return self.signals.get(pool_key)

        with mock.patch.object(svc, "TECH_SECTOR_POOL", self.pool),              mock.patch.object(svc, "SECTOR_MIN_VALID", 4),              mock.patch.object(svc, "_load_tech_greed_map", return_value={}),              mock.patch.object(svc, "_load_sector_greed_map", side_effect=AssertionError("tech7 不应加载 funds-greed")),              mock.patch.object(svc, "_compute_signal_greed", side_effect=fake_compute):
            res = svc.select_sectors(as_of="2026-06-01", enabled=True)
        self.assertEqual(res["pool_source"], "tech7")
        self.assertEqual(len(res["selected"]), 2)
        self.assertEqual(res["selected"][0]["sector"], "A")
        self.assertAlmostEqual(sum(s["weight"] for s in res["selected"]), 1.0, places=4)

    def test_tech7_empty_when_greed_map_missing(self):
        def fake_compute(pool_key, entry, greed_map, as_of, cfg=None):
            return self.signals.get(pool_key) if greed_map else None

        with mock.patch.object(svc, "TECH_SECTOR_POOL", self.pool),              mock.patch.object(svc, "SECTOR_MIN_VALID", 4),              mock.patch.object(svc, "_load_tech_greed_map", return_value={}),              mock.patch.object(svc, "_compute_signal_greed", side_effect=fake_compute):
            res = svc.select_sectors(as_of="2026-06-01")
        self.assertEqual(res["selected"], [])
        self.assertIn("空仓", res["empty_reason"])

    def test_prod10_rollback_uses_funds_greed(self):
        with mock.patch.object(
            svc, "get_sector_config", return_value={"signal_mode": "greed", "pool_source": "prod10"}
        ), mock.patch.object(svc, "SECTOR_ETF_POOL", self.pool),              mock.patch.object(svc, "SECTOR_MIN_VALID", 4),              mock.patch.object(svc, "_load_tech_greed_map", side_effect=AssertionError("prod10 不应加载 tech 贪婪")),              mock.patch.object(svc, "_load_sector_greed_map", return_value={}),              mock.patch.object(svc, "_compute_signal_greed", side_effect=lambda *a, **k: None):
            res = svc.select_sectors(as_of="2026-06-01")
        self.assertEqual(res["pool_source"], "prod10")
        self.assertEqual(res["selected"], [])


class TestSectorConfigStringMode(unittest.TestCase):
    """signal_mode 配置读写：string 类型不被 float() 破坏。"""

    def test_get_sector_config_parses_string_mode(self):
        rows = [{
            "config_key": "signal_mode", "config_value": "moneyflow",
            "label": "板块信号模式", "description": "x", "value_type": "string", "sort_order": 11,
        }]
        with mock.patch.object(svc, "_load_sector_config_rows", return_value=rows),              mock.patch.object(svc, "_cache", {}):
            svc._cache.pop("sector_config", None)
            cfg = svc.get_sector_config()
        self.assertEqual(cfg["signal_mode"], "moneyflow")
        svc._cache.pop("sector_config", None)

    def test_update_sector_config_string_value(self):
        class FakeRow:
            def __init__(self, key):
                self.config_key = key
                self.config_value = ""
                self.label = "板块信号模式"
                self.description = None
                self.value_type = "string"
                self.sort_order = 11

        row = FakeRow("signal_mode")
        db = mock.MagicMock()
        db.query.return_value.all.return_value = [row]
        with mock.patch.object(svc, "_seed_sector_config_defaults"), \
                mock.patch.object(svc, "_load_sector_config_rows", return_value=[]), \
                mock.patch("app.database.SessionLocal", return_value=db):
            svc.update_sector_config({"signal_mode": " moneyflow "})
        self.assertEqual(row.config_value, "moneyflow")  # 字符串原样存储（去空白）
        svc._cache.pop("sector_config", None)



if __name__ == "__main__":
    unittest.main()

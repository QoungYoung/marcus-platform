# -*- coding: utf-8 -*-
"""DCA 执行载体（dca-high-beta-carrier）单元测试：
载体配置解析/校验/回退、ETF 代码归一化、_build_buy_legs 载体分支与灰度开关行为。"""
import unittest
from unittest import mock
from unittest.mock import MagicMock

from app.services.golden_pit_config import DCA_CARRIER_DEFAULTS
from app.services import golden_pit_dca_service as dca
from app.services import golden_pit_industry_service as ind_svc
from app.services import golden_pit_sector_service as svc


class TestParseDcaCarrier(unittest.TestCase):
    def test_default_sector_selection(self):
        out = svc.parse_dca_carrier('', "sector_selection")
        self.assertEqual(out["mode"], "sector_selection")
        self.assertNotIn("reason", out)

    def test_fixed_combo_valid(self):
        out = svc.parse_dca_carrier(
            '{"mode":"fixed_combo","codes":[{"code":"588200","weight":0.5},{"code":"512480","weight":0.5}]}'
        )
        self.assertEqual(out["mode"], "fixed_combo")
        self.assertEqual(len(out["codes"]), 2)

    def test_invalid_json_fallback(self):
        out = svc.parse_dca_carrier("not-json{")
        self.assertEqual(out["mode"], "sector_selection")
        self.assertEqual(out["reason"], "invalid_json")

    def test_unknown_mode_fallback(self):
        out = svc.parse_dca_carrier('{"mode":"quantum"}')
        self.assertEqual(out["mode"], "sector_selection")
        self.assertEqual(out["reason"], "unknown_mode")

    def test_fixed_combo_weight_sum_must_be_one(self):
        out = svc.parse_dca_carrier('{"mode":"fixed_combo","codes":[{"code":"588200","weight":0.3}]}')
        self.assertEqual(out["mode"], "sector_selection")
        self.assertEqual(out["reason"], "weight_sum=0.30")

    def test_fixed_combo_empty_codes_fallback(self):
        out = svc.parse_dca_carrier('{"mode":"fixed_combo","codes":[]}')
        self.assertEqual(out["mode"], "sector_selection")
        self.assertEqual(out["reason"], "empty_codes")

    def test_broad_mode_ok(self):
        out = svc.parse_dca_carrier('{"mode":"broad"}')
        self.assertEqual(out["mode"], "broad")


class TestNormalizeCarrierEtfCode(unittest.TestCase):
    def test_sh_code(self):
        self.assertEqual(dca._normalize_carrier_etf_code("588200"), "SH588200")

    def test_sz_code(self):
        self.assertEqual(dca._normalize_carrier_etf_code("159949"), "SZ159949")

    def test_already_prefixed(self):
        self.assertEqual(dca._normalize_carrier_etf_code("SH512480"), "SH512480")


def _cfg(extra=None):
    c = {
        "enabled": True,
        "dca_carrier_enabled": False,
        "dca_carriers": {
            "588000": {"mode": "sector_selection", "codes": []},
            "159915": {"mode": "sector_selection", "codes": []},
        },
    }
    if extra:
        c.update(extra)
    return c


class TestCarrierBestOnly(unittest.TestCase):
    def test_best_carrier_code_picks_deepest_oversold(self):
        cfg = _cfg({"signal_mode": "greed", "pool_source": "tech7"})
        with mock.patch.object(svc, "resolve_regime_mode", return_value=("oversold", "mock")):
            with mock.patch.object(svc, "_load_tech_greed_map", return_value={}):
                with mock.patch.object(svc, "_compute_signal_greed") as csg:
                    csg.side_effect = [
                        {"sector": "科创芯片", "name": "x", "etf_code": "SH588200", "greed": 0.10, "oversold120": -0.30},
                        {"sector": "半导体", "name": "y", "etf_code": "SH512480", "greed": 0.05, "oversold120": -0.35},
                    ]
                    code, reason = svc.best_carrier_code(["588200", "512480"], "2026-08-14", cfg)
        # 512480 贪婪更低且超跌更深 → combo 更高
        self.assertEqual(code, "512480")
        self.assertIn("超跌+贪婪", reason)

    def test_best_carrier_code_trend_uses_momentum(self):
        cfg = _cfg({"signal_mode": "greed", "pool_source": "tech7"})
        with mock.patch.object(svc, "resolve_regime_mode", return_value=("trend", "mock")):
            with mock.patch.object(svc, "_compute_signal_momentum") as csm:
                csm.side_effect = [
                    {"sector": "科创芯片", "name": "x", "etf_code": "SH588200", "momentum": 0.12},
                    {"sector": "半导体", "name": "y", "etf_code": "SH512480", "momentum": 0.08},
                ]
                code, reason = svc.best_carrier_code(["588200", "512480"], "2026-08-14", cfg)
        self.assertEqual(code, "588200")
        self.assertIn("动量", reason)

    def test_best_carrier_code_no_data_returns_none(self):
        cfg = _cfg({"signal_mode": "greed", "pool_source": "tech7"})
        with mock.patch.object(svc, "resolve_regime_mode", return_value=("oversold", "mock")):
            with mock.patch.object(svc, "_load_tech_greed_map", return_value={}):
                with mock.patch.object(svc, "_compute_signal_greed", return_value=None):
                    code, reason = svc.best_carrier_code(["588200", "512480"], "2026-08-14", cfg)
        self.assertIsNone(code)
        self.assertIn("数据不足", reason)

    def test_fixed_combo_best_only_single_leg(self):
        cfg = _cfg({
            "dca_carrier_enabled": True,
            "carrier_best_only": True,
            "dca_carriers": {
                "588000": {"mode": "fixed_combo", "codes": [{"code": "588200", "weight": 0.5}, {"code": "512480", "weight": 0.5}]},
                "159915": {"mode": "sector_selection", "codes": []},
            },
        })
        with mock.patch.object(dca._sector, "get_sector_config", return_value=cfg):
            with mock.patch.object(dca._sector, "best_carrier_code", return_value=("512480", "超跌+贪婪最优")) as bc:
                with mock.patch.object(dca._sector, "select_sectors") as sel:
                    legs, notes, empty = dca._build_buy_legs("588000", [], 8000.0, "2026-08-11", "SH588000")
        self.assertEqual(empty, "")
        self.assertEqual(legs, [("carrier:512480", "SH512480", 8000.0)])
        self.assertTrue(any("载体只买最优" in n for n in notes))
        bc.assert_called_once()
        sel.assert_not_called()

    def test_fixed_combo_best_only_fallback_equal_weights(self):
        cfg = _cfg({
            "dca_carrier_enabled": True,
            "carrier_best_only": True,
            "dca_carriers": {
                "588000": {"mode": "fixed_combo", "codes": [{"code": "588200", "weight": 0.5}, {"code": "512480", "weight": 0.5}]},
                "159915": {"mode": "sector_selection", "codes": []},
            },
        })
        with mock.patch.object(dca._sector, "get_sector_config", return_value=cfg):
            with mock.patch.object(dca._sector, "best_carrier_code", return_value=(None, "候选评分数据不足")):
                with mock.patch.object(dca._sector, "select_sectors") as sel:
                    legs, notes, empty = dca._build_buy_legs("588000", [], 8000.0, "2026-08-11", "SH588000")
        self.assertEqual(empty, "")
        self.assertEqual(legs, [("carrier:588200", "SH588200", 4000.0), ("carrier:512480", "SH512480", 4000.0)])
        sel.assert_not_called()


class TestIndustryTrackGuard(unittest.TestCase):
    def test_skip_orders_when_today_already_executed(self):
        """今日已有 industry/* 日志时，重复执行只提示跳过，不再下单/写日志。"""
        cfg = {"enabled": True, "execute": True,
               "pool": [{"id": "semicon", "name": "半导体", "greed_code": "512480",
                         "etf_code": "512480", "priority": 1}],
               "pit_pct": 0.15, "drawdown_pct": 0.20, "entry_cap": 0.85,
               "cash_min_pct": 0.20, "max_total_pct": 0.12}
        px = {"512480": {"2026-08-14": 1.0}}
        adv = {
            "windows": {}, "plans": [], "exits": [], "cut_items": [],
            "allocations": [{"id": "semicon", "actual": 1000.0, "priority": 1}],
            "planned_total": 1000.0, "actual_total": 1000.0,
            "notes": [], "state": {"windows": {}},
        }
        with mock.patch.object(ind_svc, "get_industry_config", return_value=cfg), \
             mock.patch.object(ind_svc, "load_industry_greed", return_value={}), \
             mock.patch.object(ind_svc, "load_industry_px", return_value=px), \
             mock.patch.object(ind_svc, "_account_summary", return_value={"cash": 50000.0}), \
             mock.patch.object(ind_svc, "advance_industry_windows", return_value=adv), \
             mock.patch.object(dca, "_industry_track_has_records_today", return_value=True), \
             mock.patch("app.database.SessionLocal", return_value=MagicMock()), \
             mock.patch.object(dca, "_place_buy_order") as buy, \
             mock.patch.object(dca, "_place_sell_order") as sell, \
             mock.patch.object(dca, "_record_dca_log") as rec:
            out = dca._run_industry_track("2026-08-14", "2026-08-14", gate_open=True)
        buy.assert_not_called()
        sell.assert_not_called()
        rec.assert_not_called()
        self.assertTrue(any("跳过重复下单" in l for l in out["lines"]))


class TestBuildBuyLegsCarrier(unittest.TestCase):
    def test_disabled_keeps_sector_selection(self):
        with mock.patch.object(dca._sector, "get_sector_config", return_value=_cfg()):
            with mock.patch.object(dca._sector, "select_sectors", return_value={
                "selected": [{"sector": "科创芯片", "etf_code": "SH588200", "weight": 1.0}],
                "empty_reason": "",
            }) as sel:
                legs, notes, empty = dca._build_buy_legs("588000", [], 8000.0, "2026-08-11", "SH588000")
        self.assertEqual(empty, "")
        self.assertEqual(legs, [("科创芯片", "SH588200", 8000.0)])
        sel.assert_called_once()

    def test_fixed_combo_legs(self):
        cfg = _cfg({"dca_carrier_enabled": True, "dca_carriers": {
            "588000": {"mode": "fixed_combo", "codes": [{"code": "588200", "weight": 0.5}, {"code": "512480", "weight": 0.5}]},
            "159915": {"mode": "sector_selection", "codes": []},
        }})
        with mock.patch.object(dca._sector, "get_sector_config", return_value=cfg):
            with mock.patch.object(dca._sector, "select_sectors") as sel:
                legs, notes, empty = dca._build_buy_legs("588000", [], 8000.0, "2026-08-11", "SH588000")
        self.assertEqual(empty, "")
        self.assertEqual(legs, [("carrier:588200", "SH588200", 4000.0), ("carrier:512480", "SH512480", 4000.0)])
        sel.assert_not_called()

    def test_broad_mode_leg(self):
        cfg = _cfg({"dca_carrier_enabled": True, "dca_carriers": {
            "588000": {"mode": "broad"}, "159915": {"mode": "sector_selection", "codes": []},
        }})
        with mock.patch.object(dca._sector, "get_sector_config", return_value=cfg):
            legs, notes, empty = dca._build_buy_legs("588000", [], 8000.0, "2026-08-11", "SH588000")
        self.assertEqual(legs, [("carrier:broad", "SH588000", 8000.0)])

    def test_carrier_defaults_are_sector_selection(self):
        self.assertEqual(DCA_CARRIER_DEFAULTS["588000"]["mode"], "sector_selection")
        self.assertEqual(DCA_CARRIER_DEFAULTS["159915"]["mode"], "sector_selection")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""DCA 执行载体（dca-high-beta-carrier）单元测试：
载体配置解析/校验/回退、ETF 代码归一化、_build_buy_legs 载体分支与灰度开关行为。"""
import unittest
from unittest import mock

from app.services.golden_pit_config import DCA_CARRIER_DEFAULTS
from app.services import golden_pit_dca_service as dca
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

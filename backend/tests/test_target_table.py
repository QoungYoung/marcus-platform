# -*- coding: utf-8 -*-
"""L3 第三块回归：**目标底仓 = 点位→仓位对照表**（`WOLF_TARGET_TABLE`，库内默认空=关）。

语料（逐字，blueprint 仓位管理行）：「3888 收盘没站上 50%、3850 收盘跌破 35%、
3816 企稳加回 50%、站稳 3922-3930 上 70%」＋「按阶段定档 … ＋ **收盘定档**」。
⇒ 目标仓位是**收盘价对关键位的阶梯函数**，不是固定 65%；点位随行情变 ⇒ 必须可配。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")

from app.services import t_capacity as tc  # noqa: E402

SPEC = "3816:50,3850:35,3888:50,3930:70"


class TargetTableTest(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("WOLF_TARGET_TABLE", "WOLF_TARGET_TABLE_DRIVES_FLOOR")}
        os.environ.pop("WOLF_TARGET_TABLE", None)
        os.environ.pop("WOLF_TARGET_TABLE_DRIVES_FLOOR", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_off(self):
        self.assertEqual(tc.parse_target_table(), [])
        self.assertIsNone(tc.level_target_pct(4200.0))
        self.assertEqual(tc.base_floor_pct(), 65.0, "默认仍是固定 65%（逐字旧行为）")

    def test_parse(self):
        self.assertEqual(tc.parse_target_table(SPEC),
                         [(3816.0, 50.0), (3850.0, 35.0), (3888.0, 50.0), (3930.0, 70.0)])
        self.assertEqual(tc.parse_target_table("3930:70，3816:50"), [(3816.0, 50.0), (3930.0, 70.0)],
                         "支持全角逗号且按阈值升序")
        self.assertEqual(tc.parse_target_table("x:y,3900:60,,3931"), [(3900.0, 60.0)], "非法项丢弃（fail-open）")

    def test_step_lookup_matches_corpus(self):
        tbl = tc.parse_target_table(SPEC)
        for close, want in ((3800.0, 50.0), (3830.0, 50.0), (3870.0, 35.0),
                            (3900.0, 50.0), (3925.0, 50.0), (3950.0, 70.0)):
            self.assertEqual(tc.level_target_pct(close, tbl), want, "close=%s" % close)

    def test_failopen_bad_close(self):
        tbl = tc.parse_target_table(SPEC)
        self.assertIsNone(tc.level_target_pct(None, tbl))
        self.assertIsNone(tc.level_target_pct(0, tbl))

    def test_drives_floor_switch(self):
        os.environ["WOLF_TARGET_TABLE"] = SPEC
        self.assertFalse(tc.target_drives_floor())
        self.assertEqual(tc.base_floor_pct(3900.0), 65.0, "驱动开关关 ⇒ 仍用 65%")
        os.environ["WOLF_TARGET_TABLE_DRIVES_FLOOR"] = "1"
        self.assertTrue(tc.target_drives_floor())
        self.assertEqual(tc.base_floor_pct(3900.0), 50.0)
        self.assertEqual(tc.base_floor_pct(3950.0), 70.0)

    def test_two_tier_uses_table_when_driven(self):
        os.environ["WOLF_TARGET_TABLE"] = SPEC
        os.environ["WOLF_TARGET_TABLE_DRIVES_FLOOR"] = "1"
        t_off = tc.two_tier(250000.0, 50000.0)                  # 不传 close ⇒ 退回 65%
        self.assertAlmostEqual(t_off["base_target"], 162500.0)
        t_on = tc.two_tier(250000.0, 50000.0, close=3900.0)     # 点位表 ⇒ 50%
        self.assertAlmostEqual(t_on["base_target"], 125000.0)
        self.assertAlmostEqual(t_on["base_gap"], 75000.0)

    def test_wiring_and_pins(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "daily_decision.py"), encoding="utf-8").read()
        self.assertIn("level_target_pct", src)
        self.assertIn("level_table", src)
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_TARGET_TABLE", pins)
        self.assertIn("WOLF_TARGET_TABLE_DRIVES_FLOOR", pins)


if __name__ == "__main__":
    unittest.main()

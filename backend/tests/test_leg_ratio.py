# -*- coding: utf-8 -*-
"""L3 第四块回归：**腿型额度比例**（正T买 : 建仓腿 = 1 : `WOLF_LEG_RATIO_ZT_TB`，默认关）。

用户 2026-09-25 拍板：抄 **T5 的 1:1.3**（T5 是四臂里唯一赚钱的，资金结构=正 alpha 腿拿到足够名义）。
口径：正T买**当日累计额度 = 上一交易日建仓腿名义 / ratio**（拿不到参照 ⇒ 不介入，fail-open）。
另含**阶段档目标**（点位表拿不到时的备选，语料 主升75+/调整50/有风险30）。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")

from app.services import t_capacity as tc  # noqa: E402


class LegRatioTest(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("WOLF_LEG_RATIO_ZT_TB", "DATA_DIR", "WOLF_TARGET_PHASE_TABLE",
                      "WOLF_TARGET_TABLE", "WOLF_TARGET_TABLE_DRIVES_FLOOR")}
        for k in self._old:
            os.environ.pop(k, None)
        self.d = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.d

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_off(self):
        self.assertEqual(tc.leg_ratio(), 0.0)
        v, why = tc.clamp_ratio_budget(1000, 40.0, 0.0, 130000.0)
        self.assertEqual(v, 1000)
        self.assertIn("未开", why)
        self.assertEqual(tc.ratio_zt_budget(130000.0), 0.0)

    def test_budget_and_remain(self):
        os.environ["WOLF_LEG_RATIO_ZT_TB"] = "1.3"
        self.assertAlmostEqual(tc.ratio_zt_budget(130000.0), 100000.0)
        self.assertEqual(tc.clamp_ratio_budget(1500, 40.0, 0.0, 130000.0)[0], 1500, "6 万 < 10 万额度")
        self.assertEqual(tc.clamp_ratio_budget(1500, 40.0, 95000.0, 130000.0)[0], 100,
                         "剩 5,000/40 → 100 股")
        self.assertEqual(tc.clamp_ratio_budget(1500, 40.0, 100000.0, 130000.0)[0], 0, "额度用尽")

    def test_failopen_without_ref(self):
        os.environ["WOLF_LEG_RATIO_ZT_TB"] = "1.3"
        v, why = tc.clamp_ratio_budget(1000, 40.0, 0.0, 0.0)
        self.assertEqual(v, 1000, "无参照 ⇒ 不介入")
        self.assertIn("无参照", why)

    def test_state_roundtrip_and_prev_day(self):
        os.environ["WOLF_LEG_RATIO_ZT_TB"] = "1.3"
        tc.ratio_state_add("20260105", 50000)
        tc.ratio_state_add("20260105", 30000)
        tc.ratio_state_add("20260106", 90000)
        st = tc.ratio_state_load()
        self.assertAlmostEqual(st["20260105"], 80000.0, msg="同日累加")
        self.assertAlmostEqual(tc.ratio_ref_notional("20260106"), 80000.0, msg="参照=前一日")
        self.assertAlmostEqual(tc.ratio_ref_notional("20260107"), 90000.0)
        self.assertEqual(tc.ratio_ref_notional("20260105"), 0.0, "无更早的日 ⇒ 0")

    def test_phase_table(self):
        self.assertEqual(tc.parse_phase_table(), {})
        self.assertIsNone(tc.phase_target_pct("build"))
        self.assertEqual(tc.base_floor_pct_for("build"), 65.0, "驱动开关关 ⇒ 恒 65（旧行为）")
        os.environ["WOLF_TARGET_PHASE_TABLE"] = "build:75,t_only:50,defense:30,exit:0"
        os.environ["WOLF_TARGET_TABLE_DRIVES_FLOOR"] = "1"
        self.assertAlmostEqual(tc.phase_target_pct("build"), 75.0)
        for op, want in (("build", 75.0), ("t_only", 50.0), ("defense", 30.0), ("exit", 0.0)):
            self.assertAlmostEqual(tc.base_floor_pct_for(op), want, msg=op)
        self.assertEqual(tc.base_floor_pct_for("unknown"), 65.0, "未知档位 ⇒ 回退 65")

    def test_level_table_wins_over_phase(self):
        os.environ["WOLF_TARGET_PHASE_TABLE"] = "build:75"
        os.environ["WOLF_TARGET_TABLE"] = "4100:50,4200:70"
        os.environ["WOLF_TARGET_TABLE_DRIVES_FLOOR"] = "1"
        self.assertAlmostEqual(tc.base_floor_pct_for("build", close=4150.0), 50.0)

    def test_wiring_and_pins(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn("clamp_ratio_budget(", src)
        self.assertIn("ratio_state_add(", src)
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_LEG_RATIO_ZT_TB", pins)
        self.assertIn("WOLF_TARGET_PHASE_TABLE", pins)


if __name__ == "__main__":
    unittest.main()

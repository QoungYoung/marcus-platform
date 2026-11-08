# -*- coding: utf-8 -*-
"""`_round` 细分计时回归测试（2026-09-17 用户要求"装上"）。纯计时，不改判定。"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M      # noqa: E402


class RoundTimingTest(unittest.TestCase):
    def setUp(self):
        M.round_timing_reset()

    def test_buckets_present_and_reset(self):
        rt = M.round_timing()
        for k in ("regime", "cond_read", "quotes", "ledger", "loop", "other",
                  "bench", "stop_loss", "snapshot", "eval", "write"):
            self.assertIn(k, rt["secs"], k)
        self.assertEqual(rt["bars"], 0)
        M._ROUND_TIMING["quotes"] = 1.5
        M._ROUND_COUNT["bars"] = 3
        self.assertEqual(M.round_timing()["secs"]["quotes"], 1.5)
        M.round_timing_reset()
        self.assertEqual(M.round_timing()["secs"]["quotes"], 0.0)
        self.assertEqual(M.round_timing()["bars"], 0)

    def test_snapshot_is_copy(self):
        rt = M.round_timing()
        rt["secs"]["quotes"] = 99.0
        self.assertEqual(M.round_timing()["secs"]["quotes"], 0.0, "返回的必须是快照，不能改到内部状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)

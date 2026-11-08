# -*- coding: utf-8 -*-
"""拒绝类噪声去重回归测试（2026-09-17 用户："这种也会刷屏"）。

现象：同一条腿被同一道闸门反复拒（如 `趋势约束阻止卖出: MA5(7.51) > MA20(7.20)，趋势完好，
浮亏-1.84%未触发硬止损，禁止手动卖出`），每次自动执行都完整打印一行 → 刷屏。
处置：同 (标的, 腿型, 方向, 闸门标签, 当日) 的拒绝只完整报一次，重复静默计数（每 20 次提一次）；
标签把数字归一成 `#`，避免 MA 值微动被当成新原因。只影响打印，不改判定与落库。
"""
import contextlib
import io
import sys
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M      # noqa: E402

R1 = "趋势约束阻止卖出: MA5(7.51) > MA20(7.20)，趋势完好，浮亏-1.84%未触发硬止损，禁止手动卖出"
R2 = "趋势约束阻止卖出: MA5(7.62) > MA20(7.21)，趋势完好，浮亏-1.31%未触发硬止损，禁止手动卖出"
R3 = "[G6] 当日 SZ002587 sell 已 2 笔 ≥ 上限 2"


class GateNoiseDedupeTest(unittest.TestCase):
    def setUp(self):
        M._GATE_NOISE.clear()

    def test_tag_normalises_numbers(self):
        self.assertEqual(M._reason_tag(R1), M._reason_tag(R2), "MA 值微动应视为同一闸门")

    def test_first_prints_repeats_silent(self):
        k = ("SZ002587", "custom_support_sell", "sell")
        self.assertFalse(M.reject_noise_suppressed(*k, R1), "首次必须报")
        self.assertTrue(M.reject_noise_suppressed(*k, R2), "同闸门重复必须静默")
        self.assertTrue(M.reject_noise_suppressed(*k, R1))
        self.assertEqual(M._GATE_NOISE[(M.std_symbol("SZ002587"), "custom_support_sell", "sell",
                                        M._reason_tag(R1), datetime.now().strftime("%Y%m%d"))], 2)

    def test_different_gate_still_prints(self):
        k = ("SZ002587", "custom_support_sell", "sell")
        self.assertFalse(M.reject_noise_suppressed(*k, R1))
        self.assertFalse(M.reject_noise_suppressed(*k, R3), "不同闸门是不同原因，必须照报")

    def test_different_symbol_or_event_prints(self):
        self.assertFalse(M.reject_noise_suppressed("SZ002587", "custom_support_sell", "sell", R1))
        self.assertFalse(M.reject_noise_suppressed("SH603383", "custom_support_sell", "sell", R1))
        self.assertFalse(M.reject_noise_suppressed("SZ002587", "custom_vwap_sell", "sell", R1))

    def test_switch_off_always_prints(self):
        k = ("SZ002587", "custom_support_sell", "sell")
        with mock.patch.object(M, "GATE_NOISE_DEDUPE", False):
            self.assertFalse(M.reject_noise_suppressed(*k, R1))
            self.assertFalse(M.reject_noise_suppressed(*k, R1))

    def test_every_20th_suppression_prints_summary(self):
        k = ("SZ002587", "custom_support_sell", "sell")
        M.reject_noise_suppressed(*k, R1)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _ in range(20):
                M.reject_noise_suppressed(*k, R2)
        self.assertIn("累计抑制 20 次", buf.getvalue(), "不能完全无声：每 20 次要有一次提示")


if __name__ == "__main__":
    unittest.main(verbosity=2)

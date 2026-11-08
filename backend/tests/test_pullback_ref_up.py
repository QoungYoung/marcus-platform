# -*- coding: utf-8 -*-
"""「量能分层」反抽目标位开关的单测（2026-09-20）。

背景：drabj13 该族 17 笔实测实际 −4,581、+5 日反事实 Δ +7,320（净卖飞）；
只把反抽目标从 max(支撑,均价) 抬到 max(支撑,均价,前收×1.005) 的 Δ +1,386（当日完成、不加隔夜风险）。
开关 `WOLF_PULLBACK_REF_UP`（**库内默认 0**）+ `WOLF_PULLBACK_REF_UP_PCT`（默认 0.5）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.t_monitor import pullback_ref_up as R        # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._e = {k: os.environ.get(k) for k in ("WOLF_PULLBACK_REF_UP", "WOLF_PULLBACK_REF_UP_PCT")}
        for k in self._e:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._e.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestSwitch(Base):
    def test_off_by_default_is_original(self):
        """开关默认关 ⇒ 与原口径完全一致 max(支撑, 均价)（生产零影响）。"""
        self.assertEqual(R(12.0, 11.5, 12.4), 12.0)
        self.assertEqual(R(11.0, 11.5, 12.4), 11.5)

    def test_on_raises_to_prev_close(self):
        os.environ["WOLF_PULLBACK_REF_UP"] = "1"
        # 默认 PCT=0 ⇒ 目标就是前收 12.4（高于 max(支撑 12.0, 均价 11.5)）
        self.assertEqual(R(12.0, 11.5, 12.4), 12.4)
        # 加乘数时才是 12.462
        os.environ["WOLF_PULLBACK_REF_UP_PCT"] = "0.5"
        self.assertEqual(R(12.0, 11.5, 12.4), 12.462)
        # 支撑本来就更高 ⇒ 取支撑（不会往下调）
        self.assertEqual(R(13.0, 11.5, 12.4), 13.0)

    def test_on_pct_env(self):
        os.environ["WOLF_PULLBACK_REF_UP"] = "1"
        os.environ["WOLF_PULLBACK_REF_UP_PCT"] = "0"
        self.assertEqual(R(12.0, 11.5, 12.4), 12.4)          # 0% ⇒ 就是前收
        os.environ["WOLF_PULLBACK_REF_UP_PCT"] = "1.0"
        self.assertEqual(R(12.0, 11.5, 10.0), 12.0)          # 前收×1.01=10.1 < 支撑 ⇒ 取支撑

    def test_on_missing_prev_close_falls_back(self):
        os.environ["WOLF_PULLBACK_REF_UP"] = "1"
        for bad in (0, None, "", "abc"):
            self.assertEqual(R(12.0, 11.5, bad), 12.0)       # fail-safe：与原口径一致

    def test_garbage_inputs_and_pct(self):
        os.environ["WOLF_PULLBACK_REF_UP"] = "1"
        os.environ["WOLF_PULLBACK_REF_UP_PCT"] = "abc"       # 解析失败 ⇒ 落默认 0（=前收）
        self.assertEqual(R("12.0", "11.5", "12.4"), 12.4)
        self.assertEqual(R(None, None, 12.4), 12.4)          # 支撑/均价缺失 ⇒ 只看前收
        self.assertEqual(R(None, None, None), 0.0)           # 全缺 ⇒ 0（调用方会退到 current×1.005）

    def test_switch_truthy_values(self):
        for v in ("1", "true", "TRUE", "yes", "on"):
            os.environ["WOLF_PULLBACK_REF_UP"] = v
            self.assertEqual(R(12.0, 11.5, 12.4), 12.4, v)     # 默认目标=前收
        for v in ("0", "false", "no", "off", ""):
            os.environ["WOLF_PULLBACK_REF_UP"] = v
            self.assertEqual(R(12.0, 11.5, 12.4), 12.0, v)


class TestWiring(Base):
    def test_settle_uses_state_ref_up(self):
        """结算分支仍以等待态里的 ref_up 为准（抬高后「反抽到离场位」更难触发，
        摸不到就落到 14:45 尾盘确认 —— 这正是本开关的语义：不引入隔夜风险）。"""
        import inspect
        from app.services import t_monitor as M
        src = inspect.getsource(M.TMonitor._settle_pullback_sell)
        self.assertIn('p.get("ref_up")', src)
        self.assertIn("PULLBACK_END_HM", src)

    def test_waiting_state_records_prev_close_based_ref(self):
        """装配点在 _write_trigger 里（触发写入时创建等待态）。"""
        import inspect
        from app.services import t_monitor as M
        src = inspect.getsource(M.TMonitor._write_trigger)
        self.assertIn("pullback_ref_up(", src)
        self.assertIn("pre_close", src)


if __name__ == "__main__":
    unittest.main()

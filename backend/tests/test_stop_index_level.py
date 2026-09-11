# -*- coding: utf-8 -*-
"""止损② 指数大级别止损 —— 信号量化 + 接线 单测。

狼大原话（2026-08-27 549楼）:
  「**只看指数大级别**如果不走大5浪而转为下跌1浪就止损 。。。你们能不能看前面的啊。。」

口径:
  · 依「**只看大级别**」→ 判据**只取 `wave_state.level`**，不掺 sub_level/operation
    （避免又一次自造复合条件 —— S4②/P1-3 那类错误的复现路径）；
  · 本仓 level 取值域 d1/d2/d3/d4/d5/down → 「转为下跌1浪」= `level == "down"`；
  · **新鲜度护栏**：`wave_state.date` 距 today 超 `WOLF_INDEX_STOP_MAX_STALE_DAYS`（默认 3 自然日）
    → 视为过期、不触发（不拿过期浪型做清仓级动作）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "apps" / "main_line", REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import wolf_context as WC  # noqa: E402

TODAY = "20260910"


class _WaveDir:
    """把 DATA_DIR 指到临时目录并写入 wave_state.json。"""

    def __init__(self):
        self._old_dir = os.environ.get("DATA_DIR")
        # 只保存/还原, **不 pop** —— 否则调用方在进入上下文前设的 env 会被这里清掉
        self._old_env = {k: os.environ.get(k)
                         for k in ("WOLF_INDEX_LEVEL_STOP", "WOLF_INDEX_STOP_MAX_STALE_DAYS")}

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        # wolf_context.DATA 是**导入期**常量（wolf_context.py:49），改 env 不影响它 → 必须直接替换
        self._old_data = WC.DATA
        WC.DATA = self.tmp.name
        return self

    def write(self, obj):
        with open(os.path.join(self.tmp.name, "wave_state.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def __exit__(self, *a):
        WC.DATA = self._old_data
        self.tmp.cleanup()
        if self._old_dir is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self._old_dir
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _sig(wave, today=TODAY):
    with _WaveDir() as d:
        d.write(wave)
        return WC.index_level_stop(today=today)


class TestIndexLevelStopSignal(unittest.TestCase):
    def test_level_down_triggers(self):
        ok, why = _sig({"level": "down", "sub_level": "下跌浪4反弹",
                        "operation": "defense", "date": TODAY})
        self.assertTrue(ok)
        self.assertIn("2026-08-27", why)

    def test_d4_and_d5_do_not_trigger(self):
        for lv in ("d1", "d2", "d3", "d4", "d5"):
            ok, _ = _sig({"level": lv, "sub_level": "", "operation": "side", "date": TODAY})
            self.assertFalse(ok, "level=%s 不应触发" % lv)

    def test_only_level_matters_not_sub_level(self):
        """「只看指数大级别」—— sub_level 是下跌结构但 level 不是 down 时**不触发**。"""
        ok, _ = _sig({"level": "d4", "sub_level": "C杀", "operation": "defense", "date": TODAY})
        self.assertFalse(ok)
        ok2, _ = _sig({"level": "d4", "sub_level": "下跌1浪", "operation": "defense", "date": TODAY})
        self.assertFalse(ok2)

    def test_operation_does_not_matter(self):
        """operation 也不参与判据（只看大级别）。"""
        ok, _ = _sig({"level": "down", "sub_level": "", "operation": "build", "date": TODAY})
        self.assertTrue(ok)

    def test_level_case_insensitive(self):
        ok, _ = _sig({"level": "DOWN", "sub_level": "", "operation": "defense", "date": TODAY})
        self.assertTrue(ok)

    def test_missing_level_does_not_trigger(self):
        ok, why = _sig({})
        self.assertFalse(ok)
        self.assertIn("无指数浪型数据", why)

    def test_missing_file_does_not_raise(self):
        with _WaveDir() as d:
            ok, _ = WC.index_level_stop(today=TODAY)      # 目录里没有 wave_state.json
        self.assertFalse(ok)


class TestIndexLevelStopStaleness(unittest.TestCase):
    def test_stale_data_does_not_trigger(self):
        """过期浪型不触发清仓级动作。"""
        ok, why = _sig({"level": "down", "sub_level": "", "operation": "defense",
                        "date": "20260820"})            # 距 20260910 共 21 天
        self.assertFalse(ok)
        self.assertIn("过期", why)

    def test_fresh_data_triggers(self):
        ok, _ = _sig({"level": "down", "sub_level": "", "operation": "defense",
                      "date": "20260908"})              # 2 天
        self.assertTrue(ok)

    def test_threshold_configurable(self):
        os.environ["WOLF_INDEX_STOP_MAX_STALE_DAYS"] = "30"
        try:
            ok, _ = _sig({"level": "down", "sub_level": "", "operation": "defense",
                          "date": "20260820"})
            self.assertTrue(ok)
        finally:
            os.environ.pop("WOLF_INDEX_STOP_MAX_STALE_DAYS", None)

    def test_threshold_zero_means_no_staleness_tolerance(self):
        os.environ["WOLF_INDEX_STOP_MAX_STALE_DAYS"] = "0"
        try:
            ok, _ = _sig({"level": "down", "sub_level": "", "operation": "defense",
                          "date": "20260910"})
            self.assertTrue(ok)                       # 当天 → 0 天，放行
            ok2, _ = _sig({"level": "down", "sub_level": "", "operation": "defense",
                           "date": "20260909"})
            self.assertFalse(ok2)                     # 1 天 > 0 → 拦
        finally:
            os.environ.pop("WOLF_INDEX_STOP_MAX_STALE_DAYS", None)


class TestIndexLevelStopWiring(unittest.TestCase):
    """接线: 开关 + 被周期块调用（防"定义了但从不被调用"的静默失效）。"""

    def test_switch_defaults_on_and_can_be_off(self):
        self.assertNotIn("WOLF_INDEX_LEVEL_STOP", os.environ)
        self.assertNotIn(os.getenv("WOLF_INDEX_LEVEL_STOP", "1"), ("0", "false", "no"))

    def test_disabled_returns_immediately(self):
        from app.services.t_monitor import TMonitor
        os.environ["WOLF_INDEX_LEVEL_STOP"] = "0"
        try:
            TMonitor()._check_index_level_stop()      # 不应抛异常、不应做任何事
        finally:
            os.environ.pop("WOLF_INDEX_LEVEL_STOP", None)

    def test_called_in_periodic_block(self):
        import inspect
        from app.services.t_monitor import TMonitor
        src = inspect.getsource(TMonitor._run)
        self.assertIn("self._check_index_level_stop()", src)

    def test_method_exists(self):
        from app.services.t_monitor import TMonitor
        self.assertTrue(callable(getattr(TMonitor, "_check_index_level_stop", None)))


if __name__ == "__main__":
    unittest.main()

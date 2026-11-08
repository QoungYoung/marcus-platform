# -*- coding: utf-8 -*-
"""布腿账户口径修复（2026-09-18）：`rotation_switch_arm` 原来把账户硬编码成 stock。

后果（实测）：用 `--account drabsize` 跑单臂时，候选腿的买条件全写进 stock，而监控器只读 drabsize
⇒ 20 天里只成交 3 只既有做 T 票（wolf_t_rules 触发），曾被误判为"建仓广度不够"。
"""
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LegAccountTest(unittest.TestCase):
    def _src(self):
        return open(os.path.join(ROOT, "..", "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()

    def test_hardcoded_stock_is_gone(self):
        src = self._src()
        self.assertNotIn("account_id='stock'", src)
        self.assertNotIn('"account_id": "stock"', src)

    def test_uses_env_account(self):
        src = self._src()
        self.assertIn('os.getenv("T_MONITOR_ACCOUNT", "stock")', src)
        self.assertIn("account_id=%s", src)          # expire_old / held_positions 参数化
        self.assertIn('"account_id": _leg_account()', src)

    def test_default_is_stock_when_env_absent(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("rsa_probe", os.path.join(ROOT, "..", "jobs", "rotation_switch_arm.py"))
        mod = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("T_MONITOR_ACCOUNT", None)
            try:
                spec.loader.exec_module(mod)            # 生产默认口径：env 未设 → stock
            except Exception as e:
                self.skipTest("模块导入依赖环境: %s" % str(e)[:60])
            self.assertEqual(mod._leg_account(), "stock")
            os.environ["T_MONITOR_ACCOUNT"] = "drabsize"
            self.assertEqual(mod._leg_account(), "drabsize")


class LauncherTest(unittest.TestCase):
    def test_single_arm_launcher_exports_account(self):
        src = open(os.path.join(ROOT, "..", "jobs", "bt_size_run.sh"), encoding="utf-8").read()
        self.assertIn('export T_MONITOR_ACCOUNT="$ACC"', src)   # 贯穿到腿构建器/驱动子进程
        self.assertIn('export T_EXEC_ALLOWED_ACCOUNTS="$ACC"', src)


if __name__ == "__main__":
    unittest.main()

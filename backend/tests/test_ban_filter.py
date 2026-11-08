# -*- coding: utf-8 -*-
"""ban_filter 单测：G3「卖出后删票」黑名单的统一读取口（修两个静默失效）。

背景：用户 2026-09-21 问「天津普林/苏州科达这种的能挡住吗」——实测名单**已写、规则已触发**，
但 ① 读取时账户键写死 `stock`（回测账户是 drabt3）② 253/254 低吸腿根本不读这个名单。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import ban_filter as B  # noqa: E402


class TestSwitchAndAccount(unittest.TestCase):
    def setUp(self):
        for k in ("WOLF_TICKET_BAN_FIX", "T_MONITOR_ACCOUNT", "T_EXEC_ALLOWED_ACCOUNTS"):
            os.environ.pop(k, None)

    def test_default_off_keeps_history(self):
        self.assertFalse(B.enabled(), "库内默认关 ⇒ 生产零影响")
        self.assertEqual(B.accounts_to_read(), ["stock"], "关着时与修复前逐位一致")
        self.assertFalse(B.is_banned("SZ002134"), "关着时一律不拦")

    def test_account_resolution(self):
        os.environ["T_MONITOR_ACCOUNT"] = "drabt3"
        self.assertEqual(B.current_account(), "drabt3")
        os.environ["WOLF_TICKET_BAN_FIX"] = "1"
        self.assertEqual(B.accounts_to_read(), ["drabt3", "stock"], "并集：沙箱里两种键并存，只读本账户会漏一半")
        os.environ.pop("T_MONITOR_ACCOUNT", None)
        os.environ["T_EXEC_ALLOWED_ACCOUNTS"] = "drabt9,drabt8"
        self.assertEqual(B.current_account(), "drabt9")
        os.environ.pop("T_EXEC_ALLOWED_ACCOUNTS", None)
        self.assertEqual(B.current_account(), "stock", "生产没设账户 ⇒ 回落到 stock（口径不变）")
        self.assertEqual(B.accounts_to_read(), ["stock"], "生产并集退化成单元素")

    def test_symbol_forms(self):
        for s in ("002134", "SZ002134", "002134.SZ", "sz002134"):
            self.assertTrue(B.is_banned(s) in (True, False), s)   # 归一不抛错


class TestRealFile(unittest.TestCase):
    """对着真实沙箱文件验：并集口径能把两条不同账户键的禁令都读出来。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ban_")
        d = {"drabt3:SZ002134": {"since": "20260116", "symbol": "SZ002134", "account": "drabt3",
                                 "reason": "量能分层破位离场", "ttl_td": 13},
             "stock:SH603660": {"since": "20260115", "symbol": "SH603660", "account": "stock",
                                "reason": "custom_support_sell", "ttl_td": 13}}
        json.dump(d, open(os.path.join(self.tmp, "wolf_ticket_ban.json"), "w"))
        self._old = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.tmp
        os.environ["WOLF_TICKET_BAN_FIX"] = "1"
        os.environ["T_MONITOR_ACCOUNT"] = "drabt3"
        # 钉住时钟：否则 banned_symbols 会按"真实今天"把 2026-01 的条目全部过期并**落盘删掉**
        sys.path.insert(0, os.path.join(ROOT, "jobs"))
        import bt_run_pinned as brp
        brp.pin_clock("20260122")   # 落在两条禁令的 TTL(13 日) 之内；到 0128 苏州科达那条本就该过期
        # ⚠️ wolf_ticket_ban 的 STATE_FILE 是**模块级**用 DATA_DIR 算的（import 时定死）⇒
        #    测试里必须显式指到 tmp，否则会去读仓库里那份生产名单（第一版就踩了这个）。
        import app.services.wolf_ticket_ban as TB
        TB.STATE_FILE = os.path.join(self.tmp, "wolf_ticket_ban.json")

    def tearDown(self):
        for k in ("WOLF_TICKET_BAN_FIX", "T_MONITOR_ACCOUNT"):
            os.environ.pop(k, None)
        if self._old is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_both_account_keys_are_read(self):
        self.assertTrue(B.is_banned("SZ002134"), "drabt3 键（本账户）")
        self.assertTrue(B.is_banned("SH603660"), "stock 键（同一沙箱里也会出现）")
        self.assertFalse(B.is_banned("SH603986"), "没在名单里的照常放行")

    def test_file_not_mutated(self):
        p = os.path.join(self.tmp, "wolf_ticket_ban.json")
        before = json.load(open(p))
        B.banned_codes()
        after = json.load(open(p))
        self.assertEqual(len(before), len(after), "钉住时钟后不应把未到期条目过期掉")

# -*- coding: utf-8 -*-
"""止损扫描的**调用契约**与"空转可见性"单测（2026-09-17，事故复盘后补）。

事故（2026-09-17）：为省 DB 往返把 `_check_stop_loss` 里的
    `t_db.list_active_conditions(symbol=symbol, account_id=T_MONITOR_ACCOUNT)`
换成 `stop_conds_cached(symbol, account_id=…)`，但首版 helper 签名写成 `(symbol, asof_day="")`：
  · 调用点 `account_id=` → `TypeError: unexpected keyword argument 'account_id'`；
  · 即便过了第一关，helper 内部又给 `t_db.list_active_conditions` 传了它没有的 `asof_day`。
两处 TypeError 都被 `_check_stop_loss` 的 `except Exception` 吞掉 ⇒ **整段止损扫描静默空转**
（0114 一天 1192 条同形噪音；计时上只体现为 `stop_loss=0.0s`，极易被读成"优化生效"）。

本文件守住三件事：
  ① helper 的关键字必须与**真实** `t_db.list_active_conditions` 签名逐一对得上（用真实签名校验，
     而不是照抄一个可能同样写错的 stub —— 原 StopCondCacheTest 正是这么漏过去的）；
  ② 调用点写在 `t_monitor.py` 里的那次调用，复用/新增的关键字必须被 helper 接受（AST 静态校验）；
  ③ 扫描内抛异常必须**被计数并打得出来**（`stop_scan_stats()`），不能再无声无息。
"""
import ast
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "jobs"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

TM_SRC = REPO_ROOT / "backend" / "app" / "services" / "t_monitor.py"


def _strict_passthrough(real_fn):
    """把替身包成"关键字必须被真实签名接受"的严格版：多传/写错关键字立刻炸。"""
    real_sig = inspect.signature(real_fn)

    def _wrapped(**kw):
        real_sig.bind(**kw)          # 关键字与真实函数不符 → TypeError（测试要的就是这个）
        return [{"symbol": kw.get("symbol"), "trigger_kind": "custom_support_sell",
                 "account_id": kw.get("account_id")}]

    return _wrapped


class StopScanContractTest(unittest.TestCase):
    def setUp(self):
        import app.services.t_monitor as TM
        from app.services import t_db
        self.TM = TM
        self.t_db = t_db
        TM._SC_CONDS.clear()
        TM._SC_STATS.update({"hit": 0, "miss": 0})
        self._p = mock.patch.object(TM.t_db, "list_active_conditions",
                                    _strict_passthrough(self._real_lac))
        self._p.start()
        self.addCleanup(self._p.stop)

    # 真实签名的引用（mock 之前抓下来）
    @property
    def _real_lac(self):
        if not hasattr(self, "_real_lac_cached"):
            from app.services.t_db import list_active_conditions as _lac
            self._real_lac_cached = _lac
        return self._real_lac_cached

    def test_helper_accepts_account_id_kwarg(self):
        """调用点就是这么调的：`stop_conds_cached(symbol, account_id=…)`。"""
        inspect.signature(self.TM.stop_conds_cached).bind("SZ002555", account_id="stock")
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True):
            out = self.TM.stop_conds_cached("SZ002555", account_id="stock")
        self.assertTrue(out)

    def test_helper_forwards_account_id_to_db(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True):
            out = self.TM.stop_conds_cached("SZ002555", account_id="stock")
        self.assertEqual(out[0]["account_id"], "stock", "account_id 必须透传（否则会捞到别的账户的条件）")

    def test_helper_does_not_invent_kwargs(self):
        """helper 内部给 `t_db.list_active_conditions` 的关键字必须真实存在（严格替身会炸）。"""
        with mock.patch.object(self.TM, "STOP_COND_CACHE", False):
            self.TM.stop_conds_cached("SZ002555", account_id="stock")
        real = inspect.signature(self._real_lac)
        for bad in ("asof_day", "symbols", "accounts"):
            self.assertNotIn(bad, real.parameters,
                             "t_db.list_active_conditions 没有 %s；helper 不许传" % bad)

    def test_cache_key_separates_accounts(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True):
            a = self.TM.stop_conds_cached("SZ002555", account_id="stock")
            b = self.TM.stop_conds_cached("SZ002555", account_id="wolf_t")
        self.assertNotEqual(a[0]["account_id"], b[0]["account_id"], "不同账户不能共用同一条缓存")

    def test_cache_return_is_copy(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True):
            a = self.TM.stop_conds_cached("SZ002555", account_id="stock")
            a.append({"polluted": True})
            b = self.TM.stop_conds_cached("SZ002555", account_id="stock")
        self.assertEqual(len(b), 1, "调用方就地改动不得污染缓存")

    def test_invalidate_on_symbol_and_all(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True):
            self.TM.stop_conds_cached("SZ002555", account_id="stock")
            self.TM.stop_conds_cached("SH603203", account_id="stock")
            self.TM._invalidate_stop_conds("SZ002555")
            self.assertNotIn("SZ002555", [k[0] for k in self.TM._SC_CONDS])
            self.assertIn("SH603203", [k[0] for k in self.TM._SC_CONDS])
            self.TM._invalidate_stop_conds()
        self.assertEqual(self.TM._SC_CONDS, {})


class StopScanCallSiteTest(unittest.TestCase):
    """AST 静态校验：`t_monitor.py` 里那处调用只能传 helper 真有的关键字。"""

    def _calls(self):
        tree = ast.parse(TM_SRC.read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "stop_conds_cached":
                out.append(node)
        return out

    def test_call_site_exists_and_kwargs_accepted(self):
        import app.services.t_monitor as TM
        sig = inspect.signature(TM.stop_conds_cached)
        calls = self._calls()
        self.assertTrue(calls, "t_monitor.py 里应存在 stop_conds_cached 调用点")
        for c in calls:
            args = [None] * len(c.args)
            kw = {k.arg: None for k in c.keywords if k.arg}
            sig.bind(*args, **kw)        # 关键字对不上 → TypeError（正是 0114 那次事故）

    def test_no_asof_day_anywhere_in_helper(self):
        src = TM_SRC.read_text(encoding="utf-8")
        seg = src.split("def stop_conds_cached", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("asof_day", seg, "helper 不许再传 t_db 没有的 asof_day")

    def test_db_call_in_helper_uses_real_signature(self):
        """helper 体内对 `t_db.list_active_conditions` 的调用关键字，必须被真实签名接受。"""
        from app.services import t_db
        real = inspect.signature(t_db.list_active_conditions)
        tree = ast.parse(TM_SRC.read_text(encoding="utf-8"))
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "stop_conds_cached":
                fn = node
        self.assertIsNotNone(fn)
        hits = 0
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "list_active_conditions":
                hits += 1
                kw = {k.arg: None for k in node.keywords if k.arg}
                real.bind(**kw)
        self.assertGreaterEqual(hits, 1, "helper 内应有直查回退路径")


class StopScanVisibilityTest(unittest.TestCase):
    """③ 扫描异常必须计数 + 首例可见（不许再出现"整段空转但看起来变快"）。"""

    def setUp(self):
        import app.services.t_monitor as TM
        self.TM = TM
        TM.stop_scan_reset()

    def test_error_is_counted_and_first_message_kept(self):
        def _boom(*a, **k):
            raise TypeError("stop_conds_cached() got an unexpected keyword argument 'account_id'")
        inst = self.TM.TMonitor.__new__(self.TM.TMonitor)
        inst._stop_done_day = set()
        with mock.patch.object(self.TM, "stop_conds_cached", _boom):
            # 有可卖底仓 + 现价>0 才会走到 `stop_conds_cached`（否则更早就 return）
            inst._check_stop_loss("SZ002555", {"current": 10.0},
                                  {"SZ002555": {"sellable": 100}})
        st = self.TM.stop_scan_stats()
        self.assertEqual(st["err"], 1)
        self.assertIn("TypeError", st["first"])

    def test_round_timing_exposes_scan_errors(self):
        self.TM._SL_SCAN["err"] = 7
        self.TM._SL_SCAN["first"] = "TypeError: x"
        rt = self.TM.round_timing()
        self.assertEqual(rt["stop_scan_err"], 7)
        self.assertEqual(rt["stop_scan_first"], "TypeError: x")
        self.assertIn("stop_cond_cache", rt)

    def test_reset_clears(self):
        self.TM._SL_SCAN["err"] = 3
        self.TM._SL_SCAN["first"] = "TypeError: y"
        self.TM.stop_scan_reset()
        self.assertEqual(self.TM.stop_scan_stats()["err"], 0)
        self.assertEqual(self.TM.stop_scan_stats()["first"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

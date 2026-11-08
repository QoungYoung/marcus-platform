# -*- coding: utf-8 -*-
"""无底仓卖腿预筛回归测试（2026-09-17 用户拍板：生产与回测都不把无底仓卖腿送 AI 审核）。

背景：`wolf_*` 触发一律 `status='pending'` 入队，而 `claim_pending_trigger` 按 pending
**无差别认领（不筛 event_type）** → `wake_and_decide` 花一次 LLM。空仓期（如回测 25 万空仓起点）
卖腿 100% 是"必被网关裸空拦截"的废触发。

修复：入队口 `TMonitor._queue_trigger` 对卖腿先按**网关同一口径**查可卖额度，
可卖=0 → 写**终态**审计行（status='blocked' + reason 标注），不进 pending、不询 AI；
取数异常 fail-open（宁可送审不误拦）；`WOLF_NOBASE_SELL_PRESCREEN=0` 回到旧行为。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line", REPO_ROOT / "core",
           REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M            # noqa: E402
from app.services import t_gateway as G       # noqa: E402


def _sell_trig(**over):
    t = {"account_id": "stock", "condition_id": None, "symbol": "SH603383",
         "event_type": "wolf_dao_t_sell", "quote_price": 38.14, "mode": "auto",
         "reason": "近5日gain=3.6% 接近前高=True", "direction": "sell"}
    t.update(over)
    return t


class _Capture:
    """替身：记录 insert_trigger(trig, status=...) 的调用。"""

    def __init__(self):
        self.calls = []

    def __call__(self, trig, status="pending"):
        self.calls.append((dict(trig), status))
        return 900001

    @property
    def last(self):
        return self.calls[-1]


class NoBaseSellPrescreenTest(unittest.TestCase):
    def setUp(self):
        M.prescreen_cache_clear()
        self.mon = M.TMonitor()
        self.cap = _Capture()
        self._p_insert = mock.patch.object(M.t_db, "insert_trigger", self.cap)
        self._p_insert.start()
        self.addCleanup(self._p_insert.stop)
        self._p_sw = mock.patch.object(M, "NOBASE_SELL_PRESCREEN", True)
        self._p_sw.start()
        self.addCleanup(self._p_sw.stop)

    def _ledger(self, value):
        p = mock.patch.object(G, "get_sellable_ledger", lambda acc=None: value)
        p.start()
        self.addCleanup(p.stop)

    # ① 无底仓卖腿 → 终态 blocked，绝不 pending
    def test_sell_without_position_is_not_queued_for_ai(self):
        self._ledger({})
        tid = self.mon._queue_trigger(_sell_trig())
        self.assertEqual(tid, 900001)
        trig, status = self.cap.last
        self.assertEqual(status, "blocked", "无底仓卖腿必须是终态，不能 pending（否则被 AI 认领）")
        self.assertIn("无底仓卖腿不送审", trig["reason"])
        self.assertIn("近5日gain=3.6%", trig["reason"], "原触发理由必须保留在审计行里")

    # ② 只剩底仓 floor（可卖=0）同样不送审
    def test_sell_with_only_base_floor_is_blocked(self):
        self._ledger({"SH603383": {"symbol": "SH603383", "volume": 100, "sellable": 0}})
        self.mon._queue_trigger(_sell_trig())
        self.assertEqual(self.cap.last[1], "blocked")

    # ③ 有可卖底仓 → 照旧 pending（送 AI）
    def test_sell_with_position_still_pending(self):
        self._ledger({"SH603383": {"symbol": "SH603383", "volume": 5000, "sellable": 5000}})
        self.mon._queue_trigger(_sell_trig())
        trig, status = self.cap.last
        self.assertEqual(status, "pending")
        self.assertNotIn("不送审", str(trig.get("reason")))

    # ④ 买腿不受影响（无底仓买腿照旧送 AI/走建仓语义）
    def test_buy_leg_unaffected(self):
        self._ledger({})
        self.mon._queue_trigger(_sell_trig(event_type="wolf_zheng_t_buy", direction="buy"))
        self.assertEqual(self.cap.last[1], "pending")

    # ⑤ 取数异常 → fail-open（宁可送审，也不误拦真实卖腿）
    def test_ledger_error_fails_open(self):
        def _boom(acc=None):
            raise RuntimeError("db down")
        p = mock.patch.object(G, "get_sellable_ledger", _boom)
        p.start()
        self.addCleanup(p.stop)
        self.mon._queue_trigger(_sell_trig())
        self.assertEqual(self.cap.last[1], "pending")

    # ⑥ 开关关闭 → 回到旧行为
    def test_switch_off_restores_old_behaviour(self):
        self._ledger({})
        with mock.patch.object(M, "NOBASE_SELL_PRESCREEN", False):
            self.mon._queue_trigger(_sell_trig())
        self.assertEqual(self.cap.last[1], "pending")

    # ⑦ 原 trig 不被就地修改（调用方可能复用）
    def test_original_trigger_not_mutated(self):
        self._ledger({})
        t = _sell_trig()
        self.mon._queue_trigger(t)
        self.assertNotIn("不送审", str(t.get("reason")))

    # ⑧ wolf 触发入口真正走同一条预筛（端到端：_insert_wolf_trigger）
    def test_wolf_insert_path_uses_prescreen(self):
        self._ledger({})
        self.mon._insert_wolf_trigger("SH603383", "wolf_dao_t_sell",
                                      {"current": 38.14}, "近5日gain=3.6%")
        trig, status = self.cap.last
        self.assertEqual(status, "blocked")
        self.assertEqual(trig["event_type"], "wolf_dao_t_sell")
        self.assertEqual(trig.get("direction"), "sell")


if __name__ == "__main__":
    unittest.main(verbosity=2)

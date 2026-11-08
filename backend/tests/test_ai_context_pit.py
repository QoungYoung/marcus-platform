# -*- coding: utf-8 -*-
"""AI 决策上下文的 as-of 守卫单测（2026-09-17，排查"为什么第 N 跑收益变低"后补）。

守三件事：
  ① `t_db.list_ai_actions` 被夹到**回放日**：未来交易日 / 上一跑的行不得进入提示词，
     且因为上游 SQL 是 `ORDER BY id DESC LIMIT n`（未来行会挤掉过去行），必须**先多取再过滤**；
  ② 统计口径（`t_ai_agent.decision_quality` ← `t_bridge._symbol_t_stats`）跟着一起夹；
  ③ SQL 侧 `CURRENT_DATE` → `DATE '<回放日>'` 的纯文本改写正确（含 `::text` 形态）。
"""
import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "jobs"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import bt_asof_fetch as F  # noqa: E402


class GuardInstallTest(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {"BT_ASOF_FETCH": "0"}, clear=False):
            r = F.install_ai_context_guard()
        self.assertEqual(r["installed"], [])
        self.assertTrue(r["skipped"])

    def test_enabled_installs_both(self):
        with mock.patch.dict(os.environ, {"BT_ASOF_FETCH": "1"}, clear=False):
            r = F.install_ai_context_guard()
        both = " ".join(r["installed"] + r["skipped"])
        self.assertIn("list_ai_actions", both, r)
        self.assertIn("CURRENT_DATE", both, r)


class ListAiActionsPitTest(unittest.TestCase):
    """夹具：把真实 DB 换成"混了未来行"的假数据，验证夹取与多取语义。"""

    def setUp(self):
        from app.services import t_db
        self.t_db = t_db
        self.orig = t_db.list_ai_actions
        # 守卫是 BT_ASOF_FETCH 门控的：单测必须自己开（不能依赖外部 shell 恰好 export 了它——
        # 2026-09-17 实测过这种"测试靠环境变量漂绿"的坑）
        self._p0 = mock.patch.dict(os.environ, {"BT_ASOF_FETCH": "1"}, clear=False)
        self._p0.start()
        self.addCleanup(self._p0.stop)
        rows = [
            {"id": 900, "trade_date": "2026-09-15", "symbol": "SZ002195", "action_type": "ai_exec"},
            {"id": 800, "trade_date": "2026-02-04", "symbol": "SZ002195", "action_type": "ai_exec"},
            {"id": 700, "trade_date": "2026-01-19", "symbol": "SZ002195", "action_type": "ai_abandon"},
            {"id": 600, "trade_date": "2026-01-09", "symbol": "SZ002195", "action_type": "ai_exec"},
            {"id": 500, "trade_date": "2026-01-06", "symbol": "SZ002195", "action_type": "ai_wait"},
        ]
        self.rows = rows
        self.calls = []

        def _fake(trade_date=None, symbol=None, session_id=None, limit=100):
            self.calls.append(limit)
            return list(rows)

        # 用 mock.patch.object 管"植入假 DB + 装守卫"的还原：teardown 会把属性还原成 **装之前** 的样子，
        # 否则守卫 wrapper 会跨用例泄漏（第一次跑通、第二次拿到上一次的闭包 → 假数据变成陈旧闭包）。
        self._p1 = mock.patch.object(t_db, "list_ai_actions", _fake)
        self._p1.start()
        self.addCleanup(self._p1.stop)
        r = F.install_ai_context_guard()          # 在假数据之上装守卫
        self.assertTrue(any("list_ai_actions" in x for x in r["installed"]), r)
        self._p2 = mock.patch.object(F, "asof_day_str", lambda: "2026-01-09")
        self._p2.start()
        self.addCleanup(self._p2.stop)

    def test_future_rows_removed(self):
        got = self.t_db.list_ai_actions(symbol="SZ002195", limit=5)
        self.assertEqual([r["id"] for r in got], [600, 500], "只应留下回放日及以前的行")

    def test_overfetch_then_truncate(self):
        """必须多取：否则未来行挤掉过去行 → 历史上限只剩 0 条。"""
        self.calls.clear()
        got = self.t_db.list_ai_actions(symbol="SZ002195", limit=2)
        self.assertEqual(len(got), 2)
        self.assertTrue(self.calls and self.calls[0] >= 1000, "底层 limit 必须放大后再过滤：%s" % self.calls)

    def test_stats_only_count_asof(self):
        """统计口径同步被夹（`_symbol_t_stats` → `decision_quality` → `list_ai_actions`）。"""
        from app.services import t_bridge
        st = t_bridge._symbol_t_stats("SZ002195")
        self.assertEqual(st.get("total_decisions"), 2, st)

    def test_cut_counter(self):
        F._AI_PIT.update({"fetched": 0, "cut": 0, "calls": 0})
        self.t_db.list_ai_actions(symbol="SZ002195", limit=5)
        self.assertEqual(F.ai_pit_stats()["fetched"], 5)
        self.assertEqual(F.ai_pit_stats()["cut"], 3)


class SqlClockRewriteTest(unittest.TestCase):
    def test_bare_token(self):
        got = F.rewrite_current_date(
            "SELECT 1 FROM t WHERE created_at::date = CURRENT_DATE ORDER BY id DESC", "2026-01-09")
        self.assertIn("DATE '2026-01-09'", got)
        self.assertNotIn("CURRENT_DATE", got)

    def test_text_cast_form(self):
        got = F.rewrite_current_date("WHERE trade_date = CURRENT_DATE::text", "2026-01-09")
        self.assertEqual(got, "WHERE trade_date = DATE '2026-01-09'::text")

    def test_no_false_positive(self):
        sql = "SELECT current_date_col FROM t WHERE note = 'CURRENT_DATE_X'"
        self.assertEqual(F.rewrite_current_date(sql, "2026-01-09"), sql)

    def test_guard_reports_rewrite_count(self):
        self.assertEqual(set(F.sql_clock_stats()), {"rewrites", "errors"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

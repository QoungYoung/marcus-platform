# -*- coding: utf-8 -*-
"""同票历史快照（`WOLF_AGENT_HISTORY_SNAPSHOT`）回归测试。

## 为什么要它（2026-09-22 实测的"采样固化"链，用户拍板"加上历史快照"）

SH600183 0105 10:20 有一条低吸腿，被**确定性**的做T时段窗拦掉（一笔没成交）；但它的 **AI 裁决文本**
进了 prompt 的【历史决策参考（最近 3 次，含结果）】：T6 抽到 `ai_wait`（"现价 72.18 相对低吸基准仍偏高、
非深度回踩"）⇒ 0106 同票 73.88 的腿被判"高出前次低吸价 +2.35%"⇒ cancelled；T8 抽到 `ai_exec`
⇒ 历史块里**没有否定锚** ⇒ 同一条腿 executed。**一次采样差异被 prompt 历史固化并放大**。
根因：历史只来自 `t_ai_actions` 的**最近 N 条窗口**（条数随各臂决策量浮动，锚会被挤出），且全是自由文本。

本模块把历史做成**确定性快照**：直接读 `t_triggers`（腿历史，含被拦/取消）+ `paper_trades`（真实成交），
给出锚点（前次买腿价 / 前次买入成交价 / 前次卖出价）+ 现价相对锚点的偏离 + 本轮买卖笔数 + 最近腿序。

## 本测试守什么

1. **默认关**（库内 0 ⇒ prompt 与既有逐字一致，生产零影响）；
2. 快照**取数正确**：锚点、偏离 %、本轮笔数、腿序；
3. **取数失败 ⇒ 空快照**（fail-open，不得抛错、不得编造历史）；
4. 渲染：无数据 ⇒ 空串；有数据 ⇒ 含锚点与偏离行；
5. 踩过的坑留钉子：`t_triggers` **没有 trade_date 列**（日期只在 created_at）—— 写错会静默返空。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_bridge as B          # noqa: E402


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _DB:
    """假 SessionLocal：按 SQL 里的表名返回不同行集。"""

    def __init__(self, trig, trades):
        self._trig, self._trades = trig, trades

    def execute(self, sql, params=None):
        s = str(sql)
        return _Cur(self._trig if "t_triggers" in s else self._trades)

    def close(self):
        pass


TRIG = [
    {"created_at": "2026-01-05 10:20:00", "direction": "buy", "event_type": "wolf_zheng_t_buy",
     "quote_price": 72.18, "status": "blocked", "reason": "做T时段窗"},
    {"created_at": "2026-01-07 09:55:00", "direction": "sell", "event_type": "high_sell",
     "quote_price": 74.50, "status": "blocked", "reason": "G8"},
]
# ⚠️ 按**时间倒序**给（真实现是 ORDER BY created_at DESC）；假 DB 不再排序
TRADES = [
    {"trade_date": "2026-01-08", "direction": "卖出", "price": 70.04, "volume": 200},
    {"trade_date": "2026-01-06", "direction": "买入", "price": 73.88, "volume": 500},
    {"trade_date": "2026-01-05", "direction": "买入", "price": 72.18, "volume": 500},
]


class HistorySnapshotTest(unittest.TestCase):
    def _snap(self, trig=TRIG, trades=TRADES, current=73.88):
        with mock.patch("app.database.SessionLocal", lambda: _DB(trig, trades)):
            return B.symbol_history_snapshot("SH600183", account_id="acc", current=current)

    def test_default_off(self):
        self.assertFalse(B.history_snapshot_on(), "库内默认必须关（生产零影响）")
        src = (REPO_ROOT / "backend/app/services/t_bridge.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("WOLF_AGENT_HISTORY_SNAPSHOT", "0")', src)

    def test_anchors_and_deviation(self):
        s = self._snap()
        self.assertEqual(s["last_buy_leg"]["px"], 72.18)
        self.assertEqual(s["last_buy_fill"]["px"], 73.88)          # 最近一次买入成交（时间倒序第一条）
        self.assertEqual(s["last_sell"]["px"], 70.04)
        self.assertAlmostEqual(s["dev_vs_buy_leg_pct"], (73.88 / 72.18 - 1) * 100, places=2)
        self.assertAlmostEqual(s["dev_vs_last_sell_pct"], (73.88 / 70.04 - 1) * 100, places=2)

    def test_round_counts_stop_at_flat(self):
        # 倒序：卖 200（本轮）→ 买 500（本轮起点，持仓归零）⇒ 不再往前数
        s = self._snap()
        self.assertEqual(s["round"], {"buys": 1, "sells": 1})

    def test_no_current_price_no_deviation(self):
        s = self._snap(current=None)
        self.assertNotIn("dev_vs_buy_leg_pct", s)

    def test_db_error_returns_empty(self):
        def _boom():
            raise RuntimeError("db down")
        with mock.patch("app.database.SessionLocal", _boom):
            s = B.symbol_history_snapshot("SH600183", account_id="acc", current=73.88)
        self.assertEqual(s, {}, "取数失败必须返回空快照（fail-open，不得编造历史）")

    def test_render_contains_anchor_line(self):
        txt = B.render_history_snapshot(self._snap(), "SH600183")
        self.assertIn("同票历史快照", txt)
        self.assertIn("前次**买腿**", txt)
        self.assertIn("距前次买腿价", txt)
        self.assertIn("本轮（自上次清仓）", txt)

    def test_render_empty_on_no_data(self):
        self.assertEqual(B.render_history_snapshot({}, "SH600183"), "")

    def test_no_trade_date_column_pitfall(self):
        """钉子：`t_triggers` 没有 trade_date 列 —— 曾因写错列名而静默返空。"""
        src = (REPO_ROOT / "backend/app/services/t_bridge.py").read_text(encoding="utf-8")
        i = src.find("def symbol_history_snapshot")
        seg = src[i:i + 1600]
        self.assertIn("FROM t_triggers WHERE", seg)
        self.assertNotIn("SELECT trade_date", seg, "t_triggers 无 trade_date ⇒ 会抛错被吞、快照恒空")

    def test_wired_into_prompt_with_switch(self):
        src = (REPO_ROOT / "backend/app/services/t_bridge.py").read_text(encoding="utf-8")
        self.assertIn('ctx.setdefault("history_snapshot"', src)
        self.assertIn('if _HIST_SNAP:', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

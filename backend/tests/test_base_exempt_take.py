# -*- coding: utf-8 -*-
"""止盈腿「穿透底仓」扩展开关回归测试（`WOLF_BASE_EXEMPT_TAKE`，库内默认 0）。

## 背景（为什么单独一个开关）

2026-09-19 你拍板 A 的范围是「**破位/减仓语义**」的卖腿可减底仓（`WOLF_SELL_BASE_EXEMPT=1`，T6/T7 已在跑），
并**刻意**把普通做T卖腿（`high_sell`/`custom_vwap_sell` 无破位语义）留在"只动 T 仓"；
2026-09-21 方案③又用语料 2026-08-05「做T套利的仓位是做T套利的 底仓是底仓」加固了这条。

但量化（T6 全窗）显示：止盈类卖腿 676 条只成交 13 条，其中被**底仓锚**锁死 37 条，
反事实 **+4.42%（中位 +5.57%）**（腿价高于该票后来实际卖出的加权均价 ⇒ 该卖没卖）；
且账本第 2755 行早有结论：「做T卖腿只卖 T 仓、底仓不可减」**不是他的口径**，是我们为"底仓保护"加的自设约束。
语料支持扩展的那一侧：2026-09-01「**吃一口减一半**」、2025-11-23「**减仓到40%甚至更低**」、
2025-08-13「马上**减仓到盘中50%**」。

## 本开关做什么 / 不做什么

- 做：把 `high_sell / fib_target / profit_take / dao_t_sell / board_half / confirm_sell` 及 reason 含
  高抛/止盈/吃一口/兑现/减半 的腿，纳入 `base_penetrate_allowed` ⇒ 卖量走**已拍板的**穿透口径
  `_stop_exit_volume`（盘中**减半**、尾盘**清仓**）；同时让网关 0.5d 的**方案③**不再把它压回
  "近 3 个交易日买入份额"（否则开了也白开）。
- 不做：不负责节流（反复减半会啃底仓 —— 588170 事故同族）⇒ 开启后要配合"每票每轮最多 N 笔 / 仅浮盈≥阈值"观察。
- 默认关：开关关 ⇒ 与既有行为**逐位一致**（生产零影响）。
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

from app.services import t_capacity as C          # noqa: E402


class BaseExemptTakeTest(unittest.TestCase):
    def _allow(self, kind, reason, base=True, take=True, weak=False):
        with mock.patch.object(C, "BASE_EXEMPT", base), \
             mock.patch.object(C, "_BASE_EXEMPT_TAKE", take):
            return C.base_penetrate_allowed(kind, reason, struct_weak=weak)

    # ── 1. 默认关：止盈类**不能**穿透（与既有行为一致） ────────────────────
    def test_default_off_take_kinds_blocked(self):
        for k, r in (("high_sell", "高抛卖腿命中"), ("wolf_fib_target_sell", "止盈 0.618 位"),
                     ("wolf_profit_take_sell", "小赚兑现"), ("wolf_dao_t_sell", "高抛兑现")):
            self.assertFalse(self._allow(k, r, take=False), "%s 在开关关时必须不能穿透" % k)

    def test_switch_default_is_off_in_source(self):
        src = (REPO_ROOT / "backend/app/services/t_capacity.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("WOLF_BASE_EXEMPT_TAKE", "0")', src, "库内默认必须为 0（生产零影响）")

    # ── 2. 开：止盈/兑现类可穿透 ──────────────────────────────────────────
    def test_on_take_kinds_allowed(self):
        self.assertTrue(self._allow("high_sell", "高抛卖腿命中"))
        self.assertTrue(self._allow("wolf_fib_target_sell", "个股止盈点 0.618"))
        self.assertTrue(self._allow("wolf_profit_take_sell", "小赚兑现"))
        self.assertTrue(self._allow("wolf_dao_t_sell", "倒T高抛"))

    def test_on_by_reason_keyword(self):
        self.assertTrue(self._allow("", "吃一口减一半"))
        self.assertTrue(self._allow("", "兑现一半仓位"))
        self.assertTrue(self._allow("some_unknown_kind", "止盈"))

    # ── 3. 依赖关系：主开关关 ⇒ 扩展无效（不越权） ────────────────────────
    def test_extension_requires_base_switch(self):
        self.assertFalse(self._allow("high_sell", "高抛卖腿命中", base=False, take=True),
                         "WOLF_SELL_BASE_EXEMPT=0 时，止盈类也不得穿透")

    # ── 4. 非止盈腿不受影响 ──────────────────────────────────────────────
    def test_non_take_leg_unaffected(self):
        self.assertFalse(self._allow("trend_break_buy", "带量突破建仓"))
        self.assertFalse(self._allow("wolf_zheng_t_buy", "正T低吸"))

    # ── 5. 既有破位/减仓口径不回退 ───────────────────────────────────────
    def test_existing_kinds_still_allowed(self):
        self.assertTrue(self._allow("wolf_defensive_t_reduce", "", take=False))
        self.assertTrue(self._allow("custom_support_sell", "缩量破位后离场", take=False))
        self.assertTrue(self._allow("high_sell", "趋势转弱", take=False, weak=True),
                        "struct_weak（下跌结构）本来就对所有腿放行，不得因本次改动变化")

    # ── 6. 网关侧：穿透时跳过方案③（否则开了也白开） ──────────────────────
    def test_gateway_skips_take_only_cap_when_penetrating(self):
        src = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
        self.assertIn("_pen_take = bool(_tcap.base_penetrate_allowed(\"\", str(reason or \"\")))", src,
                      "网关 0.5d 必须在穿透时跳过方案③ 的 T 仓上限")
        self.assertIn("止盈腿穿透底仓（WOLF_BASE_EXEMPT_TAKE=1）", src)


class TakeQuotaTest(unittest.TestCase):
    """节流：本轮（自上次清仓以来）止盈类穿透最多 N 次（默认 2，贴语料「最多卖 2 笔」）。"""

    def _used(self, rows, maxn=2):
        with mock.patch.object(C, "_BASE_EXEMPT_TAKE_MAX", maxn), \
             mock.patch("psycopg2.connect", lambda *a, **k: _FakeConn(rows)):
            return C.take_penetrate_used("acc", "SZ000001"), C.take_penetrate_quota_ok("acc", "SZ000001")

    def test_counts_only_this_round(self):
        # 倒序扫：本轮 3 笔止盈卖（买入 100 使持仓归零 ⇒ 到此为止，更早那笔不计）
        rows = [("卖出", 100, "高抛卖腿命中"), ("卖出", 100, "高抛卖腿命中"),
                ("买入", 100, "建仓"), ("卖出", 100, "高抛"), ("买入", 200, "更早建仓")]
        used, ok = self._used(rows)
        self.assertEqual(used, 3, "只数'自上次清仓以来'的笔数（更早那笔被边界挡掉）")
        self.assertFalse(ok, "3 笔 ≥ 上限 2 ⇒ 本轮名额已用完（这正是防啃光底仓的节流）")

    def test_quota_blocks_when_exhausted(self):
        rows = [("卖出", 100, "高抛"), ("卖出", 100, "止盈"), ("买入", 500, "建仓")]
        used, ok = self._used(rows, maxn=2)
        self.assertEqual(used, 2)
        self.assertFalse(ok, "已达上限 ⇒ 不允许再穿透")

    def test_quota_allows_below_limit(self):
        rows = [("卖出", 100, "高抛"), ("买入", 500, "建仓")]
        used, ok = self._used(rows, maxn=2)
        self.assertEqual(used, 1)
        self.assertTrue(ok)

    def test_zero_means_unlimited(self):
        rows = [("卖出", 100, "高抛")] * 5 + [("买入", 900, "建仓")]
        used, ok = self._used(rows, maxn=0)
        self.assertTrue(ok, "设 0 = 不限次（文档里已标注不建议）")

    def test_fail_open_on_db_error(self):
        with mock.patch.object(C, "_BASE_EXEMPT_TAKE_MAX", 2), \
             mock.patch("psycopg2.connect", side_effect=RuntimeError("db down")):
            self.assertEqual(C.take_penetrate_used("acc", "SZ000001"), 0)
            self.assertTrue(C.take_penetrate_quota_ok("acc", "SZ000001"),
                            "取数失败按'没用过'处理 ⇒ 不额外拦（与本模块其它取数点一致的 fail-open）")

    def test_source_default_max_is_two(self):
        src = (REPO_ROOT / "backend/app/services/t_capacity.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("WOLF_BASE_EXEMPT_TAKE_MAX", "2")', src)


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def set_session(self, *a, **k):
        pass

    def cursor(self):
        return _FakeCur(self._rows)

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""④ 跨日冷却「按真实交易日差判」回归测试（`WOLF_COOLDOWN_REAL_DAYS`）。

## 修的是什么 bug（2026-09-22 T6 复盘时查出）

`t_turnover.check` 的 ④ 跨日冷却原实现：

```python
if COOLDOWN_DAYS > 0:
    _same_dir = [r for r in rows if r[0] == _side_cn]      # rows = _recent(账户, 票, 12)
    _d0 = str(_same_dir[0][3] or "")                        # 最近一次同向成交日
    if _d0 and _d0 < _today:                                # ← 只比"是不是更早的日期"
        return False, "…未满 %d 个交易日…" % COOLDOWN_DAYS    # ← COOLDOWN_DAYS 从未参与比较
```

⇒ 语义退化成「**同一票只要卖过一次，之后任何一天都不许再卖**」（文案却写着"未满 2 个交易日"）。
实测（T6/drabt6 全窗）：止盈类卖腿 **676 条只成交 13 条**，其中「跨日冷却」拦掉 **130 条**；
被拦腿的价格比该票后来实际卖出的加权均价 **高 +1.11%**（T+3 +1.50%、T+5 +2.39%）⇒ 拦错方向。
语料 2025-02-07「一个票最多买 2 笔、**卖 2 笔**」也不支持"只能卖一次"。

## 修法与边界

- 开关 `WOLF_COOLDOWN_REAL_DAYS`（**库内默认 0** ⇒ 旧行为逐字不变，生产零影响）；
- 开 ⇒ 用 `_cooldown_elapsed`（复用甲的 `_cal_days_between`）算真实交易日差：`>= COOLDOWN_DAYS` ⇒ 放行；
- **取不到日历（None）⇒ 保守拦**（与旧行为一致，不放大）；
- 作用方向 `WOLF_COOLDOWN_REAL_SCOPE`：默认 `sell`（本次目标"让止盈腿能卖"）/ `both`（买侧同 bug 一并修）；
- **只放宽不收紧**：旧行为放行的（如同一日重复）在开关开后仍然放行。
"""
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_turnover as T            # noqa: E402

NOW = datetime(2026, 4, 20, 10, 30, 0)              # 钉住的"今天"
TODAY = "2026-04-20"


def _rows(day: str, direction: str = "卖出", n: int = 1):
    return [(direction, 10.0, "%sT10:00:00" % day, day) for _ in range(n)]


class CooldownRealDaysTest(unittest.TestCase):
    def _check(self, rows, real_days, elapsed=2, scope="sell", side="sell",
               kind="wolf_fib_target_sell"):
        # GUARD_ON 库内默认关（生产零影响），回测由 pins 置 1 ⇒ 测试里显式打开
        with mock.patch.object(T, "GUARD_ON", True), \
             mock.patch.object(T, "_recent", lambda acc, sym: rows), \
             mock.patch.object(T, "_REAL_CD", real_days), \
             mock.patch.object(T, "_REAL_CD_SCOPE", scope), \
             mock.patch.object(T, "_cooldown_elapsed", lambda d0, dt: elapsed):
            # 腿型取 wolf_fib_target_sell（止盈类，且**不在 ③ 时段窗白名单**里 ⇒ 只受 ④ 约束）
            return T.check("SZ002587", side, 10.0, "drabt6",
                           reason="高抛腿 fib 目标命中", trigger_kind=kind, now=NOW)

    # ── 1. 默认关：逐字旧行为（任何"更早的日期"都拦） ──────────────────────
    def test_default_off_blocks_even_10_days_later(self):
        ok, why = self._check(_rows("2026-04-10"), real_days=False, elapsed=10)
        self.assertFalse(ok, "默认关时必须保持旧行为（哪怕过了 10 个交易日也拦）")
        self.assertIn("跨日冷却", why)
        self.assertIn("未满 2 个交易日", why)

    def test_source_default_is_off(self):
        src = (REPO_ROOT / "backend/app/services/t_turnover.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("WOLF_COOLDOWN_REAL_DAYS", "0")', src, "库内默认必须为 0")

    # ── 2. 开：按真实交易日差 ────────────────────────────────────────────
    def test_on_blocks_within_window(self):
        ok, why = self._check(_rows("2026-04-19"), real_days=True, elapsed=1)
        self.assertFalse(ok, "只隔 1 个交易日 ⇒ 仍应拦")
        self.assertIn("跨日冷却", why)

    def test_on_allows_after_window(self):
        ok, why = self._check(_rows("2026-04-17"), real_days=True, elapsed=2)
        self.assertTrue(ok, "隔满 2 个交易日 ⇒ 应放行（这就是修 bug 的目的）")
        self.assertEqual(why, "")

    # ── 3. 取不到日历 ⇒ 保守拦 ──────────────────────────────────────────
    def test_calendar_missing_blocks(self):
        ok, why = self._check(_rows("2026-04-17"), real_days=True, elapsed=None)
        self.assertFalse(ok, "日历取不到 ⇒ 保守拦（不得因缺数据放大成交量）")

    # ── 4. 只放宽不收紧 ────────────────────────────────────────────────
    def test_same_day_repeat_not_caught_by_cooldown(self):
        # 同日重复由 ①同价来回 管；④ 跨日冷却不该参与（开关开关都一样）
        for rd in (False, True):
            _, why = self._check(_rows(TODAY), real_days=rd, elapsed=0)
            self.assertNotIn("跨日冷却", why)

    def test_no_history_not_caught_by_cooldown(self):
        for rd in (False, True):
            _, why = self._check([], real_days=rd, elapsed=None)
            self.assertNotIn("跨日冷却", why)

    # ── 5. 作用方向（默认只放宽卖侧） ────────────────────────────────────
    def test_scope_sell_does_not_relax_buy(self):
        ok, why = self._check(_rows("2026-04-17", direction="买入"), real_days=True,
                              elapsed=5, scope="sell", side="buy", kind="trend_break_buy")
        self.assertFalse(ok, "默认 scope=sell ⇒ 买侧的跨日冷却保持旧行为")

    def test_scope_both_relaxes_buy(self):
        ok, _ = self._check(_rows("2026-04-17", direction="买入"), real_days=True,
                            elapsed=5, scope="both", side="buy", kind="trend_break_buy")
        self.assertTrue(ok, "scope=both ⇒ 买侧同 bug 一并修")

    # ── 6. 保护性腿仍然整段豁免（不受本次改动影响） ──────────────────────
    def test_protective_sell_still_exempt(self):
        with mock.patch.object(T, "GUARD_ON", True), \
             mock.patch.object(T, "_recent", lambda acc, sym: _rows("2026-04-19")), \
             mock.patch.object(T, "_REAL_CD", True), \
             mock.patch.object(T, "_cooldown_elapsed", lambda d0, dt: 1):
            ok, why = T.check("SZ002587", "sell", 10.0, "drabt6",
                              reason="破位止损", trigger_kind="custom_support_sell", now=NOW)
        self.assertTrue(ok)
        self.assertEqual(why, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

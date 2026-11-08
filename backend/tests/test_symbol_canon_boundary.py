# -*- coding: utf-8 -*-
"""符号规范化「边界层」回归测试（2026-09-17 纪律卖腿死腿事故 · 兜底半边）。

姊妹测试：`test_symbol_case_deadleg.py`（覆盖 ①写库前 std_symbol ②ledger_item 容错 ③预筛）。
本文件覆盖**其余会按 symbol 取数的边界**——这些地方少归一一次，同样是静默错判：

  · `gateway_execute`   唯一放行口：调用方（纪律腿/agent/接口）传小写 → 下单/成交/G6 计数全跑偏
  · `validate_order_at` 实际校验口：小写 → 账本查不到 → 恒判「无足够可卖底仓（裸空拦截）」
  · `base_floor_shares` 底仓锚：小写 → 累计买入查不到 → floor 退化 + `T_BASE_FLOOR_OVERRIDES` 永不命中
  · `_count_today_trades` G6「一个票最多卖 2 笔」：小写 → 恒 0 → 上限静默失效
  · `TMonitor._after_sell` 删票黑名单：键是 "account:SYMBOL"，小写 → 买入侧查不到 → 删票静默失效
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M            # noqa: E402
from app.services import t_gateway as G       # noqa: E402


class GatewayExecuteCanonTest(unittest.TestCase):
    """唯一放行口：小写符号必须以标准形态进入校验/下单/计数。"""

    def test_gateway_execute_normalises_before_validate(self):
        seen = {}

        def _fake_validate(symbol, side, price, volume, **kw):
            seen["symbol"] = symbol
            return {"pass": False, "reason": "test-stop", "level": "hard"}

        # gateway_execute 内部按需 import 执行器（本地测试环境没有 PySide6/vnpy）→ 用替身顶掉
        stubs = {
            "app.core.trading.marcus_trade": types.SimpleNamespace(MarcusVNPyExecutor=object),
            "paper_engine": types.SimpleNamespace(PaperTradingEngine=object),
            "workspace_detector": types.SimpleNamespace(DATA_DIR="/tmp"),
        }
        with mock.patch.dict(sys.modules, stubs), \
             mock.patch.object(G, "validate_order", _fake_validate), \
             mock.patch.object(G, "_count_today_trades", lambda *a, **k: 0):
            res = G.gateway_execute("sz002587", "sell", 6.97, 100, account_id="stock")
        self.assertEqual(seen.get("symbol"), "SZ002587",
                         "gateway_execute 必须在入口把符号归一为系统标准形态")
        self.assertIn("test-stop", str(res.get("reason")))


class ValidateOrderAtCanonTest(unittest.TestCase):
    """小写符号 + 大写账本键 → 不得再判「无足够可卖底仓（裸空拦截）」。"""

    def _ctx(self, ledger):
        return {"regime": "ACTIVE", "quote": {"current": 100.0, "pre_close": 100.0},
                "ledger": ledger, "net_asset": 200000.0, "daily": {}, "risk": {},
                "sell_in_transit": False, "trigger_status": None, "cost_ratio_ok": True,
                "account_id": "stock"}

    def test_lowercase_symbol_finds_ledger(self):
        ledger = {"SZ002587": {"symbol": "SZ002587", "volume": 4600, "sellable": 4600,
                               "today_buy": 0, "avg_price": 6.5}}
        with mock.patch.object(G, "base_floor_shares", lambda *a, **k: 100):
            res = G.validate_order_at("sz002587", "sell", 6.97, 100, self._ctx(ledger))
        self.assertNotIn("裸空", str(res.get("reason")),
                         "小写形态必须能查到同一份账本（旧行为：恒判裸空 → 卖腿结构性死掉）")

    def test_unknown_symbol_still_blocked(self):
        with mock.patch.object(G, "base_floor_shares", lambda *a, **k: 100):
            res = G.validate_order_at("sz002587", "sell", 6.97, 100, self._ctx({}))
        self.assertIn("裸空", str(res.get("reason")), "真无持仓必须照旧拦截（不因容错而放行）")


class BaseFloorCanonTest(unittest.TestCase):
    """底仓锚按标准形态取数（小写会让累计买入查不到、覆盖表永不命中）。"""

    def test_lowercase_reaches_cum_buy_with_canonical_symbol(self):
        seen = {}

        def _fake_cum(account_id, symbol):
            seen["symbol"] = symbol
            return 400

        fake_impl = types.SimpleNamespace(base_floor_shares=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
        with mock.patch.object(G, "_cum_buy_volume", _fake_cum), \
             mock.patch.dict(sys.modules, {"app.services.t_base_floor": fake_impl}):
            floor = G.base_floor_shares("stock", "sz002587", volume=200)
        self.assertEqual(seen.get("symbol"), "SZ002587", "底仓锚必须用标准形态查累计买入")
        self.assertEqual(floor, 200, "400×0.5 → 200")


class TradeCapCountCanonTest(unittest.TestCase):
    """G6 笔数统计：小写符号必须换算成标准形态再查 paper_trades。"""

    def test_count_today_trades_sql_param_is_canonical(self):
        captured = {}

        class _Cur:
            def execute(self, sql, params):
                captured["sql"] = sql
                captured["params"] = params

            def fetchone(self):
                return (2,)

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        fake_pg = types.SimpleNamespace(connect=lambda *a, **k: _Conn())
        with mock.patch.dict(sys.modules, {"psycopg2": fake_pg}):
            n = G._count_today_trades("stock", "sz002587", "sell")
        self.assertEqual(n, 2)
        self.assertEqual(captured["params"][1], "SZ002587",
                         "G6 计数用小写符号会恒返 0 → 「一个票最多卖 2 笔」静默失效")


class AfterSellBanCanonTest(unittest.TestCase):
    """破线卖出「删票」：黑名单键必须用标准形态（否则买入侧查不到 → 删票静默失效）。"""

    def test_after_sell_bans_canonical_symbol(self):
        calls = []
        fake_tb = types.SimpleNamespace(ban=lambda acct, sym, why: calls.append((acct, sym, why)))
        with mock.patch.dict(sys.modules, {"app.services.wolf_ticket_ban": fake_tb}):
            M.TMonitor()._after_sell("sh600584", "stock", "custom_support_sell", "破位")
        self.assertEqual(calls and calls[0][:2], ("stock", "SH600584"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

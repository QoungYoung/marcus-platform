# -*- coding: utf-8 -*-
"""卖腿「底仓 floor」**执行**（`WOLF_SELL_FLOOR_ENFORCE`）回归测试 —— 2026-09-22。

## 病灶（实测 data/_bt_t5 全窗）

提示词自己要求「`volume`=计划卖出量（**≤可卖底仓，保留底仓**）」（`prompt_seeds.py:968`），
但**网关从不执行 floor**：`validate_order_at` 的卖腿分支只做"裸空"断言
（`sellable < volume` 才拒），且 `sellable <= floor` 时直接 `pass`（跳过检查）
⇒ **AI 显式给量的止盈/高抛腿能把底仓一起卖掉**。

证据链：
- T5 清仓 31 次中 **18 次是止盈/高抛腿把持仓打到 0**（3 月 3 次、**4 月 12/16**）；
- 归零那笔的理由原文：「高抛卖腿且**可卖底仓200股充足**（成本42.11/现价43.12浮盈2.40%）」；
- 典型 SH600584 0410：可卖 400 / floor 200，当天 100+100+**200** 三笔卖光
  （第三笔正落在 `sellable<=floor` 的 `pass` 分支）；
- 后果：底仓归零 → 写 reset → 「建仓→高抛清仓→再建仓」循环。实测 T5 清仓次数
  1月2 / 2月3 / **3月10 / 4月16**，持仓期中位 8→10→**3→3** 天，
  **3-12 起"老仓（买入 ≥4 交易日）占比"恒为 0%** ⇒ 底仓再也建不起来
  ⇒ 反弹段（市场 +9.6%、持仓端 +22.7%）组合只吃到 +5.7%。

## 语料

- 2026-08-05「**做T套利的仓位是做T套利的 底仓是底仓**」/「分不清…就是贪」
- 2025-11-23「…**减仓到40%甚至更低**」、2025-08-13「马上**减仓到盘中50%**」
  ⇒ 减仓/止损**允许穿底仓**，故本开关对止血类**豁免**。

## 本测试守什么

1. **默认关 ⇒ 逐字零变化**（返回原量、归因写明"未介入"）——生产零影响；
2. 开 ⇒ 卖量收敛到 **T 仓空间 = max(可卖 − 底仓, 0)**（只收紧，永不放大）；
3. 开 ⇒ **止损/破位/风控/减仓类豁免**（止血必须能执行），`is_stop_loss=True` 同样豁免；
4. 开 ⇒ T 仓空间为 0 时返回 0（调用方据此拒绝，底仓不可卖）；
5. **取数失败/无券可卖 ⇒ 放行原值**（fail-safe，不因异常误拦）；
6. **正向判据**：只作用于止盈类；且**不被文案「未破止损」骗到**（见 test_reason_text_cannot_fake_exemption）。
"""
import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import t_gateway as gw  # noqa: E402


def _cap(volume, sellable, floor, reason="高抛卖腿", is_stop_loss=False, enabled=True):
    """打桩 ledger + floor，调用 sell_floor_cap。

    默认 reason = 止盈类（本闸只作用于止盈类；非止盈类一律放行见 test_non_take_legs_pass_through）。
    """
    env = {"WOLF_SELL_FLOOR_ENFORCE": "1" if enabled else "0"}
    with mock.patch.dict(os.environ, env, clear=False):
        # 开关是模块级常量 ⇒ 直接打桩该常量（等价于进程启动时已置 1）
        with mock.patch.object(gw, "_SELL_FLOOR_ENFORCE", enabled), \
             mock.patch.object(gw, "base_floor_shares", return_value=floor), \
             mock.patch.object(gw, "get_sellable_ledger",
                               return_value={"SZ000001": {"sellable": sellable}}):
            return gw.sell_floor_cap("stock", "SZ000001", volume,
                                     sellable=sellable, reason=reason, is_stop_loss=is_stop_loss)


class SellFloorCapTest(unittest.TestCase):

    def test_default_off_is_identity(self):
        """开关关 ⇒ 原值返回，且归因写明未介入（生产零影响）。"""
        for v, sel, fl in ((300, 400, 200), (400, 400, 200), (100, 100, 100), (0, 0, 0)):
            got, why = _cap(v, sel, fl, enabled=False)
            self.assertEqual(got, v)
            self.assertIn("未介入", why)

    def test_caps_to_t_shop(self):
        """开 ⇒ 收敛到 可卖 − 底仓（这里 400−200=200）。"""
        got, why = _cap(400, 400, 200)
        self.assertEqual(got, 200)
        self.assertIn("底仓floor", why)
        self.assertIn("200", why)

    def test_never_raises(self):
        """只收紧：卖量小于 T 仓空间时原样返回。"""
        got, why = _cap(100, 400, 200)
        self.assertEqual(got, 100)
        self.assertIn("在 T 仓空间内", why)

    def test_zero_room(self):
        """仅剩底仓 ⇒ 返回 0（调用方据此拒绝，底仓不可卖）。"""
        got, _ = _cap(200, 200, 200)
        self.assertEqual(got, 0)
        got2, _ = _cap(100, 100, 200)      # floor 甚至大于可卖
        self.assertEqual(got2, 0)

    def test_odd_lot_floor_to_100(self):
        """T 仓空间取整到一手（300−100=200）。"""
        got, _ = _cap(300, 300, 100)
        self.assertEqual(got, 200)

    def test_non_take_legs_pass_through(self):
        """**非止盈类一律放行**：止损/破位/减仓/未知（止血动作必须能执行）。"""
        for rs in ("②趋势线止损：跌破 60 日底线", "条件命中自动执行（custom_support_sell）",
                   "风控减仓 derisk_cut", "避险减仓", "[量能分层] 尾盘确认离场",
                   "防御性减仓卖腿：现价73.41=建议价", "防守减仓卖腿正向兑现"):
            got, why = _cap(400, 400, 200, reason=rs)
            self.assertEqual(got, 400, f"{rs} 应放行")
            self.assertIn("未介入", why)
        got, why = _cap(400, 400, 200, reason="高抛卖腿", is_stop_loss=True)
        self.assertEqual(got, 400)
        self.assertIn("未介入", why)

    def test_take_profit_legs_are_capped(self):
        """止盈/高抛类正是要治的那一类 ⇒ 收敛到 T 仓空间。"""
        for rs in ("高抛卖腿且可卖底仓200股充足", "高抛卖腿兑现：现价39.94等于建议卖价",
                   "fib目标高抛卖腿兑现利润", "止盈卖腿正向兑现", "wolf_profit_take_sell 命中"):
            got, _ = _cap(400, 400, 200, reason=rs)
            self.assertEqual(got, 200, f"{rs} 应收敛")

    def test_reason_text_cannot_fake_exemption(self):
        """**回归守卫**（2026-09-22 真 bug）：止盈腿的理由常写「未破止损」「无止损价可破」，
        第一版用"豁免关键词"（含「止损」）的反向排除 ⇒ 被文案骗到 ⇒ 假豁免、闸等于没开
        （实测 T10 前 8 天 7 笔止盈腿有 4 笔被误放行）。正向判据必须不被这些文案骗到。
        """
        trap = ("高抛卖腿命中且口径一致（现价92.12=建议卖价92.12，无脱节），未破止损（②不成立）、"
                "无止损价可破（非③）、regime 允许")
        got, why = _cap(400, 400, 200, reason=trap)
        self.assertEqual(got, 200, "含「未破止损」的止盈腿被误放行 ⇒ 假豁免回归")
        self.assertNotIn("未介入", why)
        # 反向也要守住：真正的止损腿仍放行
        self.assertEqual(_cap(400, 400, 200, reason="②趋势线止损：跌破 60 日底线")[0], 400)

    def test_applies_predicate(self):
        self.assertTrue(gw.sell_floor_applies("高抛卖腿兑现"))
        self.assertFalse(gw.sell_floor_applies("条件命中自动执行（custom_support_sell）"))
        self.assertFalse(gw.sell_floor_applies(""))
        self.assertFalse(gw.sell_floor_applies("高抛卖腿", is_stop_loss=True))

    def test_failsafe_on_error(self):
        """取数异常 ⇒ 放行原值（不因异常误拦）。"""
        with mock.patch.object(gw, "_SELL_FLOOR_ENFORCE", True), \
             mock.patch.object(gw, "base_floor_shares", side_effect=RuntimeError("boom")):
            got, why = gw.sell_floor_cap("stock", "SZ000001", 400, sellable=400, reason="高抛卖腿")
            self.assertEqual(got, 400)
            self.assertIn("放行原值", why)

    def test_no_sellable_passthrough(self):
        """无券可卖 ⇒ 不介入（交给既有"裸空"断言处理，不重复拦）。"""
        got, why = _cap(400, 0, 0)
        self.assertEqual(got, 400)
        self.assertIn("未介入", why)


class GatewayExecuteEndToEndTest(unittest.TestCase):
    """端到端：`gateway_execute`（做T下单**唯一入口**）真的把收敛后的量交给执行器。

    只在"纯函数对了但没接进唯一入口"这类事故上兜底（本项目踩过多次）。
    需要 PG：`DATABASE_URL=postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading`
    （本机 18789 不可达；不设则代码回落 `postgres:5432` 会挂住 ⇒ 用 skipUnless 显式跳过）。
    """

    def setUp(self):
        self.dsn = os.getenv("DATABASE_URL", "")
        if "5433" not in self.dsn:
            self.skipTest("未设 DATABASE_URL(5433) ⇒ 跳过端到端（见 AGENTS.md 测试节）")

    def _run(self, volume, capv, reason="高抛卖腿"):
        import types
        from app.services import t_gateway as g
        calls = {}

        class _Exec:
            def __init__(self, engine=None, account_id=""):
                pass

            def sell(self, symbol=None, price=None, volume=None, reason="", **kw):
                calls["sell"] = {"symbol": symbol, "volume": volume}
                return {"status": "success", "orderid": 1}

        fake_pe = types.ModuleType("paper_engine")
        fake_pe.PaperTradingEngine = lambda **kw: None
        fake_mt = types.ModuleType("app.core.trading.marcus_trade")
        fake_mt.MarcusVNPyExecutor = _Exec
        with mock.patch.dict(sys.modules, {"paper_engine": fake_pe,
                                           "app.core.trading.marcus_trade": fake_mt}), \
             mock.patch.object(g, "validate_order", return_value={"pass": True, "reason": ""}), \
             mock.patch.object(g, "sell_floor_cap", return_value=(capv, "底仓floor测试")), \
             mock.patch.object(g, "_trade_cap", return_value=0), \
             mock.patch.object(g, "std_symbol", side_effect=lambda s: s):
            res = g.gateway_execute("SZ000001", "sell", 10.0, volume, reason=reason,
                                    decision_source="ai_led", account_id="stock")
        return res, calls

    def test_capped_volume_reaches_executor(self):
        _res, calls = self._run(400, 200)
        self.assertEqual(calls.get("sell", {}).get("volume"), 200,
                         "收敛后的量没交给执行器 ⇒ 接线断了")

    def test_zero_cap_rejects_without_order(self):
        res, calls = self._run(400, 0)
        self.assertEqual(res.get("status"), "rejected")
        self.assertIn("底仓floor", res.get("reason", ""))
        self.assertNotIn("sell", calls, "收敛为 0 却仍下单")

    def test_uncapped_passthrough(self):
        _res, calls = self._run(150, 150)
        self.assertEqual(calls.get("sell", {}).get("volume"), 150)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class GatewayExecuteWiringTest(unittest.TestCase):
    """接线守卫（AST 级）：`gateway_execute` 是"做T下单**唯一入口**"——

    纯函数写对了但没接进唯一入口 = 等于没做（本项目踩过多次）。这里用 AST 断言三件事：
      ① 卖腿分支里调了 `sell_floor_cap`；
      ② 该调用**在** `executor.sell(...)` **之前**（否则收敛了也没用）；
      ③ 收敛为 0 时**必须拒绝**（不能"收敛成 0 还下单"）。

    ⚠️ 为什么不用 mock 跑端到端：`gateway_execute` 里有 DB/网络依赖（PG/腾讯行情），
    测试环境会挂住；改用 AST 守卫（与 `test_crowding_blacklist_removed.py` 同风格）。
    """

    def setUp(self):
        self.ast = ast
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.tree = ast.parse(src)
        self.fn = next(n for n in ast.walk(self.tree)
                       if isinstance(n, ast.FunctionDef) and n.name == "gateway_execute")

    def _lines(self, pred):
        return sorted(n.lineno for n in ast.walk(self.fn) if pred(n))

    def test_cap_called_before_executor_sell(self):
        cap = self._lines(lambda n: isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Name) and n.func.id == "sell_floor_cap")
        ex = self._lines(lambda n: isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute) and n.func.attr == "sell")
        self.assertTrue(cap, "gateway_execute 里没有调用 sell_floor_cap ⇒ 接线断了")
        self.assertTrue(ex, "找不到 executor.sell 调用点")
        self.assertLess(min(cap), min(ex), "floor 收敛发生在 executor.sell 之后 ⇒ 等于没做")

    def test_zero_cap_rejected(self):
        """收敛为 0 时必须 rejected（不能继续下单）。"""
        import ast
        src = ast.dump(self.fn)
        self.assertIn("rejected", src)
        # 拒绝分支与 _capv 判断同处（粗粒度但能挡住"把拒绝删掉"）
        self.assertIn("_capv", src)

    def test_switch_is_read_from_env_with_default_off(self):
        """开关默认必须是 0（生产零影响），且从 env 读。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_SELL_FLOOR_ENFORCE", "0")', src)


class GatewayExecuteEndToEndTest(unittest.TestCase):
    """端到端：`gateway_execute`（做T下单**唯一入口**）真的把收敛后的量交给执行器。

    只在"纯函数对了但没接进唯一入口"这类事故上兜底（本项目踩过多次）。
    需要 PG：`DATABASE_URL=postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading`
    （本机 18789 不可达；不设则代码回落 `postgres:5432` 会挂住 ⇒ 用 skipUnless 显式跳过）。
    """

    def setUp(self):
        self.dsn = os.getenv("DATABASE_URL", "")
        if "5433" not in self.dsn:
            self.skipTest("未设 DATABASE_URL(5433) ⇒ 跳过端到端（见 AGENTS.md 测试节）")

    def _run(self, volume, capv, reason="高抛卖腿"):
        import types
        from app.services import t_gateway as g
        calls = {}

        class _Exec:
            def __init__(self, engine=None, account_id=""):
                pass

            def sell(self, symbol=None, price=None, volume=None, reason="", **kw):
                calls["sell"] = {"symbol": symbol, "volume": volume}
                return {"status": "success", "orderid": 1}

        fake_pe = types.ModuleType("paper_engine")
        fake_pe.PaperTradingEngine = lambda **kw: None
        fake_mt = types.ModuleType("app.core.trading.marcus_trade")
        fake_mt.MarcusVNPyExecutor = _Exec
        with mock.patch.dict(sys.modules, {"paper_engine": fake_pe,
                                           "app.core.trading.marcus_trade": fake_mt}), \
             mock.patch.object(g, "validate_order", return_value={"pass": True, "reason": ""}), \
             mock.patch.object(g, "sell_floor_cap", return_value=(capv, "底仓floor测试")), \
             mock.patch.object(g, "_trade_cap", return_value=0), \
             mock.patch.object(g, "std_symbol", side_effect=lambda s: s):
            res = g.gateway_execute("SZ000001", "sell", 10.0, volume, reason=reason,
                                    decision_source="ai_led", account_id="stock")
        return res, calls

    def test_capped_volume_reaches_executor(self):
        _res, calls = self._run(400, 200)
        self.assertEqual(calls.get("sell", {}).get("volume"), 200,
                         "收敛后的量没交给执行器 ⇒ 接线断了")

    def test_zero_cap_rejects_without_order(self):
        res, calls = self._run(400, 0)
        self.assertEqual(res.get("status"), "rejected")
        self.assertIn("底仓floor", res.get("reason", ""))
        self.assertNotIn("sell", calls, "收敛为 0 却仍下单")

    def test_uncapped_passthrough(self):
        _res, calls = self._run(150, 150)
        self.assertEqual(calls.get("sell", {}).get("volume"), 150)


if __name__ == "__main__":
    unittest.main(verbosity=2)

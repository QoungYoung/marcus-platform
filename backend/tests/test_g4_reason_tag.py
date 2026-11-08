# -*- coding: utf-8 -*-
"""G4 成交的**可观测性标记**（`WOLF_G4_REASON_TAG`）回归测试 —— 2026-09-23。

## 病灶（自动开发第 2 轮）

G4「被动止盈线只上移、不破不卖」（狼大 2025-06-09 / 2026-07-01）**一直在正常工作**：

| 臂 | G4 触发 | 触发行→AI exec | 实际成交 |
|---|---|---|---|
| drabt5 | 20 次 | 8 次 | **5 笔** |
| drabt9 / t10 / t11 / t12 | 各 1 次 | — | 各 **1 笔** |

但成交的 `reason` 是 **AI 自己写的自由文本**，实测写成
「被动**止损**卖腿触发／命中／兑现／按触发快照执行」——
于是用 `reason LIKE '%被动止盈%'` 统计 G4 效果时**一笔都查不到**，
导致连续两次误判「G4 在回测里失效」：
  ① 第一次误在查了被回测重置清表的 `t_triggers`（sequence 227653，表里只剩 47 行）；
  ② 第二次误在用了 `reason LIKE '%被动止盈%'` 这个筛子。

## 本测试守什么

1. **开关关 ⇒ 逐位不变**（生产零影响）；
2. 开 ⇒ `wolf_passive_stop_sell` 腿的 reason 被加上 `[G4 被动止盈] ` 前缀；
3. 开 ⇒ **非 G4 腿一字不改**（止损/破位/止盈/建仓…）；
4. 幂等：已带 `[G4` 的 reason 不重复加；
5. reason 为空/None 时给兜底文案且**不抛**；trigger 为 None 同样不抛；
6. 接线静态检查：`t_ai_agent` 的 `gateway_execute(...)` 用的是打过标记的 reason；
   且 pins / 驱动把开关在回测置 1、库内默认值是 `"0"`。
"""
import ast
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import t_ai_agent as ai  # noqa: E402


def _tag(reason, ev, on=True):
    with mock.patch.object(ai, "G4_REASON_TAG", on):
        return ai._tag_g4_reason(reason, {"event_type": ev} if ev else {})


class G4ReasonTagTest(unittest.TestCase):

    def test_default_off_is_identity(self):
        for r in ("被动止损卖腿触发：as-of现价45.19=建议卖价", "", None, "AI 决策执行"):
            self.assertEqual(_tag(r, "wolf_passive_stop_sell", on=False), r)

    def test_on_tags_g4_leg(self):
        got = _tag("被动止损卖腿触发：as-of现价45.19=建议卖价", "wolf_passive_stop_sell")
        self.assertTrue(got.startswith("[G4] "))
        self.assertIn("45.19", got)

    def test_on_leaves_other_legs_untouched(self):
        for ev in ("stop_loss", "custom_support_sell", "wolf_profit_take_sell",
                   "trend_break_buy", "", None):
            src = "条件命中自动执行（%s）" % ev
            self.assertEqual(_tag(src, ev), src, "非 G4 腿不应被改：%s" % ev)

    def test_tag_adds_no_decision_keyword(self):
        """★ 中性性：标记**不引入新的关键词命中**（对比打标记前后的命中集合必须相等）。

        `t_capacity._BASE_EXEMPT_TAKE_KW`（止盈穿透名额 / 能否穿底仓）与
        `t_protect.PROTECT_KW`（保护性卖出，传统模式）都会**按 reason 关键词**判定；
        只要标记让原本不命中变成命中，就会改变交易行为（2026-09-23 实测：原前缀
        `[G4 被动止盈]` 会命中「止盈」「被动止盈」⇒ 已改为零命中的 `[G4]`）。

        ⚠️ 尤其注意 `t_capacity._BASE_EXEMPT_KW`（含**「止损」**）—— 且 `WOLF_SELL_BASE_EXEMPT=1`
        在回测里是**开着**的，而 G4 的 reason 实测写着「被动**止损**卖腿…」⇒ **G4 能否穿透底仓
        正依赖于这两个字**（见账本 §8.16）。标记**只能加、不能删**原文本，否则会反转该判定。
        """
        from app.services import t_capacity as cap
        from app.services import t_protect as tp
        kws = (tuple(cap._BASE_EXEMPT_KW) + tuple(cap._BASE_EXEMPT_TAKE_KW)
               + tuple(tp.PROTECT_KW))
        for src in ("破位确认应执行减仓", "被动止损卖腿触发：现价45.19跌破", "x", ""):
            got = _tag(src, "wolf_passive_stop_sell")
            before = {k for k in kws if k in (src or "")}
            after = {k for k in kws if k in got}
            self.assertEqual(before, after,
                             "标记引入了新的关键词命中：%s ⇒ %s（src=%r）" % (before, after, src))
            self.assertEqual(got, "[G4] " + (src or "AI 决策执行"))

    def test_idempotent(self):
        once = _tag("x", "wolf_passive_stop_sell")
        twice = _tag(once, "wolf_passive_stop_sell")
        self.assertEqual(once, twice)
        self.assertEqual(twice.count("[G4"), 1)

    def test_empty_reason_gets_fallback(self):
        got = _tag(None, "wolf_passive_stop_sell")
        self.assertTrue(got.startswith("[G4] "))
        self.assertIn("AI 决策执行", got)

    def test_no_trigger_does_not_raise(self):
        with mock.patch.object(ai, "G4_REASON_TAG", True):
            self.assertEqual(ai._tag_g4_reason("r", None), "r")


class WiringTest(unittest.TestCase):

    def _src(self, rel):
        return open(os.path.join(ROOT, rel), encoding="utf-8").read()

    def test_gateway_call_uses_tagged_reason(self):
        src = self._src("backend/app/services/t_ai_agent.py")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if f != "gateway_execute":
                    continue
                for kw in node.keywords:
                    if kw.arg == "reason":
                        seg = ast.get_source_segment(src, kw.value) or ""
                        if "_tag_g4_reason" in seg:
                            found = True
        self.assertTrue(found, "AI 腿执行的 gateway_execute reason 未走 _tag_g4_reason")

    def test_library_default_off(self):
        src = self._src("backend/app/services/t_ai_agent.py")
        self.assertIn('os.getenv("WOLF_G4_REASON_TAG", "0")', src)

    def test_pins_and_driver_on_for_backtest(self):
        self.assertIn("WOLF_G4_REASON_TAG='1'", self._src("jobs/bt_env_pins.sh"))
        self.assertIn('("WOLF_G4_REASON_TAG", "1")', self._src("jobs/bt_prod_run.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

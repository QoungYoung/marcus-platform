# -*- coding: utf-8 -*-
"""G4 底仓穿透的**结构化**资格（`WOLF_BASE_EXEMPT_G4`）回归测试 —— 2026-09-23。

## 病灶（账本 §8.16）

`wolf_passive_stop_sell`（狼大 2025-06-09「不破被动止盈根本不会卖」）能不能穿底仓，
**取决于 AI 有没有在成交 reason 里写「止损」二字**：

- `WOLF_SELL_BASE_EXEMPT=1`（回测 pins）⇒ `base_penetrate_allowed` 的**关键词路径是活的**；
- `_BASE_EXEMPT_KW` 含「止损」；而 AI 看着腿型名 `wolf_passive_stop_sell`（字面含 stop）
  把 G4 写成「被动**止损**卖腿触发/命中/兑现」⇒ 命中 ⇒ 允许穿底仓；
- ⇒ **语义由错标签承载**：一旦有人「好心」把标签修正成「被动止盈」，G4 会**静默失去**该资格
  （实测：`reason="被动止盈线跌破：…"` ⇒ `base_penetrate_allowed` 返回 **False**）。

## 本开关做什么

让资格由**腿型**决定，与 2026-09-22 拍板 A′ 把 `t_protect` 改结构化同向：
开启后 `wolf_passive_stop_sell` 无论 reason 怎么写（哪怕不含任何关键词）都能穿底仓。
**库内默认 0**；开启会改变行为（原本被挡的 G4 卖腿开始减底仓）⇒ 需显式拍板。

## 本测试守什么

1. 开关关 ⇒ **逐位不变**（G4 仍只看关键词：写「止损」才允许、写「被动止盈」不允许）；
2. 开关开 ⇒ G4 **与文案无关**（「被动止盈」「破位减仓」「什么都不写」都允许）；
3. 开关开 ⇒ **非 G4 腿一字不改**；
4. 主开关 `WOLF_SELL_BASE_EXEMPT=0` ⇒ 一律 False（本开关**从属**于主开关）；
5. `struct_weak=True` 的既有路径不变；
6. **静态守卫**：库内默认值是 `"0"`；且 `jobs/bt_env_pins.sh` **此刻不得**启用它
   —— 2026-09-23 有一条 T13 单臂正在跑，pins 一改就会让该轮**前后半段口径不一致**。
"""
import ast
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import t_capacity as cap  # noqa: E402

G4 = "wolf_passive_stop_sell"
G4_REASON_STOP = "被动止损卖腿触发：as-of现价45.19=建议卖价（脱节0%）"
G4_REASON_TAKE = "被动止盈线跌破：现价 45.19 < 被动止盈线 47.19（狼大 2025-06-09 不破不卖）"
G4_REASON_BARE = "该腿按触发快照执行"


def _allowed(kind, reason, g4=False, master=True, struct_weak=False):
    with mock.patch.object(cap, "_G4_STRUCT", g4), \
         mock.patch.object(cap, "BASE_EXEMPT", master):
        return cap.base_penetrate_allowed(trigger_kind=kind, reason=reason, struct_weak=struct_weak)


class G4StructuredTest(unittest.TestCase):

    def test_default_off_is_byte_identical(self):
        """开关关 ⇒ 行为与现状逐位一致：G4 仍**只**看关键词。"""
        self.assertTrue(_allowed(G4, G4_REASON_STOP, g4=False), "写「止损」⇒ 现状就是允许")
        self.assertFalse(_allowed(G4, G4_REASON_TAKE, g4=False),
                         "写「被动止盈」⇒ 现状不允许（这正是错标签承载语义的证据）")
        self.assertFalse(_allowed(G4, G4_REASON_BARE, g4=False))

    def test_on_makes_g4_label_independent(self):
        """开关开 ⇒ G4 与文案无关（这正是本开关的目的）。"""
        for r in (G4_REASON_STOP, G4_REASON_TAKE, G4_REASON_BARE, ""):
            self.assertTrue(_allowed(G4, r, g4=True), "G4 应允许穿底仓（reason=%r）" % r)

    def test_on_does_not_touch_other_legs(self):
        """开关开 ⇒ 非 G4 腿一字不改（普通做T卖腿仍只动 T 仓）。"""
        for k in ("custom_vwap_sell", "high_sell", "custom_support_sell", "wolf_build", ""):
            self.assertFalse(_allowed(k, G4_REASON_BARE, g4=True),
                             "非 G4 腿不该被放行：%s" % k)

    def test_subordinate_to_master_switch(self):
        """主开关关 ⇒ 一律 False（本开关从属，不会绕过主开关）。"""
        self.assertFalse(_allowed(G4, G4_REASON_TAKE, g4=True, master=False))
        self.assertFalse(_allowed(G4, G4_REASON_STOP, g4=True, master=False))

    def test_struct_weak_path_unchanged(self):
        """既有的 struct_weak 路径不受影响。"""
        self.assertTrue(_allowed("custom_vwap_sell", "", g4=True, struct_weak=True))
        self.assertTrue(_allowed("custom_vwap_sell", "", g4=False, struct_weak=True))


class StaticGuardTest(unittest.TestCase):

    def test_library_default_is_off(self):
        src = open(os.path.join(ROOT, "backend/app/services/t_capacity.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_BASE_EXEMPT_G4", "0")', src)

    def test_not_enabled_in_pins_while_t13_runs(self):
        """⚠️ 2026-09-23：T13 单臂在跑，pins 一旦启用本开关，该轮前后半段口径会不一致。

        等 T13 跑完（或用户拍板）后，把本断言改成"pins 置 1"即可 —— 这是**有意的临时守卫**。
        """
        pins = open(os.path.join(ROOT, "jobs/bt_env_pins.sh"), encoding="utf-8").read()
        self.assertNotIn("export WOLF_BASE_EXEMPT_G4='1'", pins,
                         "T13 在跑，禁止在 pins 启用该开关（会让该轮口径前后不一致）")
        drv = open(os.path.join(ROOT, "jobs/bt_prod_run.py"), encoding="utf-8").read()
        self.assertNotIn('("WOLF_BASE_EXEMPT_G4", "1")', drv)

    def test_g4_kind_is_in_structured_set(self):
        src = open(os.path.join(ROOT, "backend/app/services/t_capacity.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if getattr(t, "id", "") == "_BASE_EXEMPT_G4_KINDS":
                        found = "wolf_passive_stop_sell" in ast.get_source_segment(src, node.value)
        self.assertTrue(found, "_BASE_EXEMPT_G4_KINDS 必须含 wolf_passive_stop_sell")


if __name__ == "__main__":
    unittest.main(verbosity=2)

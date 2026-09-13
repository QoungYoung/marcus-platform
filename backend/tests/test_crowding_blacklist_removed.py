# -*- coding: utf-8 -*-
"""回归守卫：拥挤黑名单机制已于 2026-09-13 **真删**（用户指令，非停用）。

背景（勿重新引入）：
  · 该机制 = `rotation_universe.build_crowding_blacklist()` 产出 `data/crowding_blacklist.json`
    → `indicator.check_entry_filters` 硬拦（`hard_block=True; downgrade_multiplier=0`）
    → 以及 `wolf_confirm_pick` / `rotation_switch_arm` / `rotation_switch_agent` /
      `rotation_switch_dryrun` 的**静默排除**（`ts in detail → continue`，无日志无提示）。
  · 删除依据：D15 事件研究（jobs/eval_d15_crowd_blacklist.py，
    docs/wolf-d15-crowd-blacklist-event-study.md）——被拦组未来 5 日 +3.689% vs 非拥挤
    +0.250%（块状 t=1.39 不显著）→ 整体拦反；且「公募重仓」这条腿无统计支撑
    （D11：主题层 IC≈0、个股层 IC +0.019、加排除后基线 +0.683%→−0.152%）。

本测试用 AST 检查（**注释与 docstring 不算**，只有真实代码里的标识符/字符串才算），
因此可以在源码里保留「此处曾实现 X，已于某日删除」的说明性注释而不误报。
"""
import ast
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 曾经实现/消费该机制的运行时文件
ENFORCEMENT_FILES = [
    "backend/app/api/indicator.py",
    "apps/main_line/wolf_confirm_pick.py",
    "jobs/rotation_switch_arm.py",
    "jobs/rotation_switch_agent.py",
    "jobs/rotation_switch_dryrun.py",
]
# 曾经产出该机制的文件
GENERATOR_FILE = "apps/main_line/rotation_universe.py"

# 机制专属的字符串常量（出现在真实代码里即为重新引入）
FORBIDDEN_STRINGS = {"crowding_blacklist.json", "symbols_detail"}


def _tree(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return ast.parse(f.read(), filename=rel)


class TestCrowdingBlacklistRemoved(unittest.TestCase):

    def test_no_forbidden_string_literals_in_enforcement_path(self):
        """选票/布腿/下单路径里不得再出现 crowding_blacklist.json / symbols_detail。"""
        for rel in ENFORCEMENT_FILES:
            with self.subTest(file=rel):
                bad = [n.value for n in ast.walk(_tree(rel))
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and n.value in FORBIDDEN_STRINGS]
                self.assertEqual(bad, [], f"{rel} 仍引用已删除的拥挤黑名单机制: {sorted(set(bad))}")

    def test_no_crowd_space_reason_helper(self):
        """indicator.py 的个股豁免辅助函数 _crowd_space_reason 已随硬拦删除。"""
        names = {n.name for n in ast.walk(_tree("backend/app/api/indicator.py"))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("_crowd_space_reason", names)

    def test_blacklist_generator_is_gone(self):
        """rotation_universe.py 不再定义 build_crowding_blacklist。"""
        tree = _tree(GENERATOR_FILE)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("build_crowding_blacklist", names)
        calls = [n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn("build_crowding_blacklist", calls)

    def test_artifact_is_not_shipped(self):
        """仓库里不应再带着黑名单产物（data/*.json 虽被 gitignore，本地/生产也不该留）。"""
        artifact = os.path.join(ROOT, "data", "crowding_blacklist.json")
        self.assertFalse(os.path.exists(artifact),
                         "data/crowding_blacklist.json 仍在；该机制已真删，产物应一并移除")


if __name__ == "__main__":
    unittest.main()

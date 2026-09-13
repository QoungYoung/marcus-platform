# -*- coding: utf-8 -*-
"""回归守卫：vreb_reversal（量窒息+顺风反包 → 做T底仓候选）整块已于 2026-09-13 **真删**。

删除依据（勿重新引入）：
  1. 数据层 `backend/app/services/t_vreb_reversal.py` 顶层 `import duckdb`，而 duckdb
     **从未写进 `backend/requirements.txt`** → 镜像里没有 → 相关端点调用即 500
     （ModuleNotFoundError）。
  2. 它读的是 **parquet 数据湖**（`data/股票数据/行情数据/stock_daily.parquet`、
     `data/指数数据/index_daily/000300.SH.parquet`），**该数据已不存在**（本地与生产都没有）
     → 即使装上 duckdb 也无法运行。用户 2026-09-13 确认：parquet 数据已不存在，不必再装。
  3. 生产 `t_build_scan_results` 里 **`source='vreb_reversal'` 0 行** → 该特性从未在生产产出过数据，
     纯读库的那个端点永远是空。
  4. 无任何调用方：`config/tasks.yaml` 无 vreb 任务、`worker_main` 未注册该模块、
     前端 `TAccountPage` 调的是 `/vrebounce/...`。

一并删除：`backend/app/services/t_vreb_reversal.py`、`backend/tests/test_t_vreb_reversal.py`、
`backend/app/api/t_account.py` 里的三个端点。
**如需该策略**：请基于生产 PG 的 `mkt_bars_daily` 重写数据层，不要再依赖 parquet+duckdb。

本测试用 AST 检查（**注释/docstring 不算**），因此源码里可保留「此处曾实现 X、已于某日删除」的说明。
"""
import ast
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVICE = "backend/app/services/t_vreb_reversal.py"
TEST_FILE = "backend/tests/test_t_vreb_reversal.py"
API = "backend/app/api/t_account.py"


def _tree(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return ast.parse(f.read(), filename=rel)


class TestVrebReversalRemoved(unittest.TestCase):

    def test_service_and_test_files_are_gone(self):
        for rel in (SERVICE, TEST_FILE):
            with self.subTest(file=rel):
                self.assertFalse(os.path.exists(os.path.join(ROOT, rel)),
                                 f"{rel} 仍存在；该特性已真删（parquet 数据湖已不存在）")

    def test_no_module_reference_in_api(self):
        """t_account.py 的运行代码里不得再 import t_vreb_reversal（注释不算）。"""
        tree = _tree(API)
        mods = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                mods.append(n.module or "")
        self.assertFalse([m for m in mods if "t_vreb_reversal" in m],
                         f"{API} 仍 import 已删除的 t_vreb_reversal: {mods}")

    def test_no_vreb_reversal_routes(self):
        """t_account.py 里不得再有 vreb 相关路由（装饰器字符串常量）。"""
        tree = _tree(API)
        routes = [n.value for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and ("vreb/reversal" in n.value or "vreb_reversal" in n.value)]
        self.assertEqual(routes, [], f"{API} 仍挂着 vreb 路由: {routes}")


if __name__ == "__main__":
    unittest.main()

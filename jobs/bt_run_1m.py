# -*- coding: utf-8 -*-
"""bt_run_1m.py — **按 1min bar 重放年跑**的启动器（不改 `bt_prod_run.py` / `bt_agent_loop.py`）。

它存在的唯一理由：把"从 5min 切到 1min"这件事**收敛成一个进程内的 3 处替换**，写在一处、
可审计、可一键回退；`bt_prod_run.py` 本体（含 `--mins/--bars-db/--day/--agent` 等参数）保持原样。

替换的 3 个名字（`bt_local_market.enable_1m_mode(bpr)`）
------------------------------------------------------
| # | `bt_prod_run` 里的名字 | 原值 | 1min 模式 |
| - | --- | --- | --- |
| ① | `LocalMarket(a.mins, a.bars_db, day)`（L186 类 / L633 实例化） | 5min 库 | `bt_local_market.build_market(..., mode="1m")` → `LocalMarket1m`（真 m1 + 由 1min 聚合 m5） |
| ② | `install_data_shims(market, hhmm_ref)`（L351） | m1/m5 都返回 m5（降级冒充） | `bt_local_market.install_data_shims`（**频率感知**：m1 真 m1、m5 真 m5） |
| ③ | `BAR_MINUTES`（L54，48 根 5min） | 09:35…15:00 | `BAR_MINUTES_1M`（241 根 1min：09:30…15:00） |

**等价的手改 diff（若你更愿意直接改 `bt_prod_run.py`，就这 4 处、共 6 行）**
```diff
@@ 文件头 import 区（约 L70，`REPO = bt_env.REPO` 之后）
+import bt_local_market as _blm            # 1min 本地库（新增文件，不改别的）

@@ L633（`market = LocalMarket(a.mins, a.bars_db, day)`）
-    market = LocalMarket(a.mins, a.bars_db, day)
+    market = _blm.build_market(day, mins1=os.getenv("BT_MINS1", os.path.join(REPO, "data/_bt_full/mins1")),
+                               mins=a.mins, bars_db=a.bars_db,
+                               mode=os.getenv("BT_BAR_MODE", "5m"))     # 默认仍是 5m，零行为变化

@@ L635（`install_data_shims(market, hhmm_ref)`）
-    install_data_shims(market, hhmm_ref)
+    (_blm.install_data_shims if getattr(market, "mode", "5m") == "1m" else install_data_shims)(market, hhmm_ref)

@@ L776（`bars = list(BAR_MINUTES)`）
-    bars = list(BAR_MINUTES)
+    bars = list(_blm.BAR_MINUTES_1M if getattr(market, "mode", "5m") == "1m" else BAR_MINUTES)
```
（`getattr(market, "mode", "5m")` 对老 `LocalMarket` 恒为 `"5m"` → 不设 `BT_BAR_MODE=1m` 时**逐字节等价**。）

用法
----
    python jobs/bt_run_1m.py --day 20260320                 # 只跑一天，1min 重放
    python jobs/bt_run_1m.py --day 20260320 --dry-arm        # 其它参数原样透传给 bt_prod_run

    # 只对照不切换（安全）：
    python jobs/bt_run_1m.py --day 20260320 --what-if        # 打印将要替换的 3 个名字后退出

可选环境变量：
    BT_MINS1      1min 缓存目录（默认 data/_bt_full/mins1）
    BT_SYNC_PREV  `daily`（默认，历史日聚合成一根日线）/ `full`（历史日也写分钟）
    BT_STRICT     1 → 用"完成时刻"网格（09:31…15:01）+ `label < hhmm`（无前视；241 步）
"""
from __future__ import annotations

import os
import sys

sys.path[:0] = []
import bt_env  # noqa: E402
bt_env.add_paths()

REPO = bt_env.REPO


def main() -> int:
    argv = sys.argv[1:]
    what_if = "--what-if" in argv
    argv = [x for x in argv if x != "--what-if"]

    import bt_prod_run as bpr
    import bt_local_market as blm

    info = blm.enable_1m_mode(
        bpr,
        mins1=os.getenv("BT_MINS1", os.path.join(REPO, "data", "_bt_full", "mins1")),
        prev_days=int(os.getenv("BT_SYNC_PREV_DAYS", "45")),
        sync_prev=os.getenv("BT_SYNC_PREV", "daily"),
        strict_lookahead=os.getenv("BT_STRICT", "0") not in ("0", "", "false", "no"),
    )
    print("[run1m] 已切到 1min 重放：LocalMarket→%s / install_data_shims→频率感知 / BAR_MINUTES=%d 根"
          % (info["market"].__name__ if hasattr(info["market"], "__name__") else "factory",
             info["bars"]), file=sys.stderr)
    print("[run1m] 1min 缓存=%s  sync_prev=%s  strict=%s"
          % (os.getenv("BT_MINS1", os.path.join(REPO, "data", "_bt_full", "mins1")),
             os.getenv("BT_SYNC_PREV", "daily"), os.getenv("BT_STRICT", "0")), file=sys.stderr)
    if what_if:
        print("[run1m] --what-if：不执行回放。等价手改 diff 见 jobs/bt_run_1m.py 头注释。")
        return 0
    sys.argv = [sys.argv[0]] + argv
    return bpr.main()


if __name__ == "__main__":
    sys.exit(main())

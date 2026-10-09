# -*- coding: utf-8 -*-
"""wolf_eod_review_chain.py — 盘后复盘链（合并 A3 + C2 + B1）。

用户 2026-10-09：「这几个不能合并成一个任务吗」⇒ 能，合并为**一条链**。
原来三条独立 cron：
  · 18:45 `wolf_index_futures`      （A3 股指期货多空 → 次日黄白线预判）
  · 18:50 `wolf_theme_resilience`   （C2 方向层「跌得少弹得早」）
  · 19:40 `wolf_review_score_run`   （B1 每日复盘打分表）
合并理由：① 三条都是"**盘后算 → 次日盘前读**"的同一节拍；② 三条脚本开头**都各自调
`wolf_eod.gate()`** 做 EOD 就绪守卫（共用前置 ✓）；③ 合并后 **1 个 cron / 1 份日志 / 1 次告警**，
产物时效一致；实测耗时 27s + 0s + 69s ≈ 100s ≪ 任务超时（900s）✓。

**隔离性**：逐步 `try/except`，任一步失败**不阻断**后续步骤 ✓；最后汇总退出码
（有失败 ⇒ 非 0 ⇒ 调度器记 failed 并按 `on_failure` 推 QQ ✓）。

⚠️ **为什么 B4（涨停梯队/连板结构）没并进来**：它的中继源（promax `limit_list_d`）
**当晚 18:40 还没发布**（实测 2026-10-08 晚 3 次尝试全部 0 行 ⇒ failed；2026-10-09 早上
同一接口返回 88 行 ✓）⇒ 它必须跑在**次日盘前**（已改 08:05 ✓ 见 tasks.yaml），
与这三条的时窗不同，故保持独立。

用法: python jobs/wolf_eod_review_chain.py [YYYYMMDD] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

# (模块文件名, 展示名) —— 顺序即执行顺序（B1 最慢放最后，失败也不挡前面 ✓）
STEPS = (
    ("wolf_index_futures", "A3 股指期货多空→次日黄白线"),
    ("wolf_theme_resilience", "C2 跌得少弹得早"),
    ("wolf_review_score", "B1 每日复盘打分表"),
)


def _run_step(mod_name, label, argv_extra):
    """跑一步（**绝不抛** ✓）。返回 (label, rc, 秒)。"""
    import importlib
    import time
    t0 = time.time()
    rc = 1
    try:
        mod = importlib.import_module(mod_name)
        _old = sys.argv
        sys.argv = [mod_name + ".py"] + list(argv_extra)   # 显式传参，避免继承本链的 argv ✗
        try:
            rc = int(mod.main() or 0)
        finally:
            sys.argv = _old
    except SystemExit as e:
        rc = int(e.code or 0)
    except Exception as e:
        print("[chain] %s 异常: %s: %s" % (label, type(e).__name__, str(e)[:160]))
        rc = 1
    return label, rc, round(time.time() - t0, 1)


def main():
    extra = list(sys.argv[1:])          # 透传 [YYYYMMDD] / [--dry-run] / [--days N]
    print("[chain] 盘后复盘链开始 | 参数=%s" % (extra or "无"))
    results = [_run_step(m, lab, extra) for m, lab in STEPS]
    bad = [r for r in results if r[1] != 0]
    for lab, rc, secs in results:
        print("[chain]   %-28s rc=%-3s %6.1fs" % (lab, rc, secs))
    print("[chain] 汇总: %d 步，失败 %d 步%s"
          % (len(results), len(bad), ("：" + ", ".join(r[0] for r in bad)) if bad else " ✓"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

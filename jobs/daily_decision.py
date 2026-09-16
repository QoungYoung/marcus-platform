# -*- coding: utf-8 -*-
"""daily_decision.py — G1 每日决策对象（盘后 19:45 先于存档 / 盘前 08:25 确认）。

把当天 L1 方向 / L2 档位 / L3 仓位 / L4 选票 / L5 买点 / L6 兑现汇成
`data/decision/<date>.json`，供次日盘中所有腿引用（`daily_decision.entry_allowed()`）。
开关 `WOLF_DAILY_DECISION`（默认 0）。

**EOD 就绪闸的探针日必须与运行时段匹配（2026-09-16 修）**：
  · 盘后 19:45（daily_decision）→ 默认 `--eod-probe self`：当天日线已发布，就绪闸有意义；
  · 盘前 08:25（daily_decision_am）→ **必须** `--eod-probe last-closed`：开盘前当天日线
    必然 0 行，用 self 会每天白等 6×300s 后 rc=2 → AM 对象永不产出（09-15/09-16 实锤）。

用法: python jobs/daily_decision.py [--date YYYYMMDD] [--dry-run] [--eod-probe self|last-closed|none]
      （也可用环境变量 WOLF_EOD_PROBE，命令行优先）
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_EOD_PROBE = "self"


def _arg(name: str, default=None):
    """取 `--name value` 形式的参数（缺值 → 返回 default）。"""
    if name in sys.argv:
        try:
            return sys.argv[sys.argv.index(name) + 1]
        except Exception:
            return default
    return default


def eod_probe() -> str:
    """探针模式：命令行 `--eod-probe` > 环境变量 `WOLF_EOD_PROBE` > self。"""
    v = _arg("--eod-probe") or os.getenv("WOLF_EOD_PROBE", "") or DEFAULT_EOD_PROBE
    return str(v).strip()


def main():
    dry = "--dry-run" in sys.argv
    d8 = _arg("--date")
    try:
        from app.services.daily_decision import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[decision] 导入失败: {e}")
        return 1
    if not enabled():
        print("[decision] WOLF_DAILY_DECISION=0 → 跳过")
        return 0
    probe = eod_probe()
    _g = gate(d8, probe=probe)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(d8=d8, save=not dry)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

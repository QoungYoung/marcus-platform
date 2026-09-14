# -*- coding: utf-8 -*-
"""fetch_m5_replay.py — 为 **428 条参考触发腿**（bt_pit replay）预取 m5，用于阶段 1 正式验收。

为什么需要：阶段 1 的验收样本（生产腿 n=33）太小、H1/H2 会变号。参考腿 n=428 是现成的、
时间跨度 2025-07-10 → 2026-08-31 的样本，但只有日线（`ReplayBars`）→ 跑不了真·分时黄线（V2/V3）。
真 m5 数据源已验证可回溯到 2025-07（`stk_mins` 每日 48 根）。

策略：**只拉需要的日子**（每腿 arm_date 起 6 个交易日），按 ts_code 合并去重 → 约 2000 次请求
（166 个符号），串行 + 间隔≥1s（卖家要求）。缓存与 fetch_m5_legs 共用 `data/m5_local/m5/<ts_code>.json`。

用法：
  .venv/bin/python jobs/fetch_m5_replay.py            # 幂等续拉；中断可重跑
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_CACHE = ROOT / "data" / "m5_local"


def need_map(hold_pad: int = 6):
    """{ts_code: [YYYYMMDD, ...]}：每腿 arm_date 起 hold_pad 个交易日（含当日）。"""
    import eval_leg_metrics as M
    from app.services.t_backtest_data import resolve_trade_days
    legs = M.load_replay_legs()
    alld = [str(d)[:8] for d in resolve_trade_days("20250701", "20260920")]
    idx = {d: i for i, d in enumerate(alld)}
    need = collections.defaultdict(set)
    skipped = 0
    for l in legs:
        i = idx.get(l["arm_date"])
        if i is None:
            skipped += 1
            continue
        for d in alld[i:i + hold_pad]:
            need[l["ts_code"]].add(d)
    return {k: sorted(v) for k, v in need.items()}, len(legs), skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--pad", type=int, default=6, help="每腿取 arm_date 起 N 个交易日")
    args = ap.parse_args()

    need, n_legs, skipped = need_map(args.pad)
    cache = Path(args.cache)
    total = sum(len(v) for v in need.values())
    print("[m5-replay] 腿 %d（跳过 %d），符号 %d，请求 %d" % (n_legs, skipped, len(need), total))

    from app.services.t_backtest_data import prefetch_m5
    man_p = cache / "replay_manifest.json"
    man = {"n_legs": n_legs, "symbols": len(need), "requests": total, "results": {}}
    if man_p.exists():
        try:
            man = json.loads(man_p.read_text(encoding="utf-8"))
            man["results"] = man.get("results") or {}
        except (ValueError, OSError):
            pass
    gaps, done, fetched = list(man.get("gaps") or []), 0, 0
    for sym, days in sorted(need.items()):
        prev = man["results"].get(sym) or {}
        done += 1
        try:
            r = prefetch_m5(sym, days, cache, is_index=False, ts_code=sym)
        except Exception as e:  # noqa: BLE001 —— 不把失败当"没有"
            r = {"fetched": 0, "gaps": [{"key": sym, "reason": "%s: %s" % (type(e).__name__, str(e)[:80])}]}
        g = r.get("gaps") or []
        man["results"][sym] = {"days": len(days), "fetched": r.get("fetched"),
                               "gaps": len(g), "prev": prev.get("fetched")}
        gaps += g
        fetched += int(r.get("fetched") or 0)
        print("[m5-replay] (%d/%d) %s 新增 %s 天 / 缺口 %d" % (done, len(need), sym, r.get("fetched"), len(g)),
              flush=True)
        man["gaps"] = gaps
        man["fetched_total"] = int(man.get("fetched_total") or 0) + int(r.get("fetched") or 0)
        cache.mkdir(parents=True, exist_ok=True)
        man_p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[m5-replay] 完成：本轮新增 %d 天，累计缺口 %d → %s" % (fetched, len(gaps), man_p))
    if gaps:
        print("[m5-replay] 缺口示例：", json.dumps(gaps[:5], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

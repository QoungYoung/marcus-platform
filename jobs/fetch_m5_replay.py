# -*- coding: utf-8 -*-
"""fetch_m5_replay.py — 为 **428 条参考触发腿**（bt_pit replay）预取 m5，用于阶段 1 正式验收。

为什么需要：阶段 1 的验收样本（生产腿 n=33）太小、H1/H2 会变号。参考腿 n=428 是现成的、
时间跨度 2025-07-10 → 2026-08-31 的样本，但只有日线（`ReplayBars`）→ 跑不了真·分时黄线（V2/V3）。
真 m5 数据源已验证可回溯到 2025-07（`stk_mins` 每日 48 根）。

策略：**只拉需要的日子**（每腿 arm_date 起 6 个交易日），按 ts_code 合并去重 → 约 2000 次请求
（166 个符号），串行 + 间隔≥1s（卖家要求）。缓存与 fetch_m5_legs 共用 `data/m5_local/m5/<ts_code>.json`。

用法：
  .venv/bin/python jobs/fetch_m5_replay.py                 # 逐日（幂等续拉；慢，约 5s/请求）
  .venv/bin/python jobs/fetch_m5_replay.py --range         # **区间模式**（快 ~10x，推荐）
      └ 实测：stk_mins 支持 start_date/end_date 区间，一次返回多日（119 日/4.4s、160 日/10.9s），
        而逐日调用每次约 5s → 同一份数据 100+ 请求 vs 1 请求。区间长按 ≤180 自然日切片（防单次行数上限）。
      └ `--keep-all` 会把区间内**不需要的**日期也留在缓存里（默认只留需要的日子，避免缓存膨胀）。
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


def fetch_range(ts_code: str, days, cache: Path, keep_all: bool = False,
                chunk_days: int = 180) -> dict:
    """**区间模式**：按需要的日子 min→max 切块，一次请求取回多日，再按日拆开落盘。"""
    from datetime import datetime, timedelta
    from app.services import t_backtest_data as T
    import json as _json
    target = cache / "m5" / ("%s.json" % ts_code)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if target.exists():
        try:
            existing = _json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = {}
    want = set(days)
    need = sorted(d for d in want if len(existing.get(d) or []) < 40)
    if not need:
        return {"fetched": 0, "gaps": []}
    pro = T._get_brze_pro()
    freq = T.M5_FREQ
    gaps, got = [], 0
    lo = datetime.strptime(need[0], "%Y%m%d")
    hi = datetime.strptime(need[-1], "%Y%m%d")
    cur = lo
    while cur <= hi:
        end = min(cur + timedelta(days=chunk_days - 1), hi)
        T._brze_rate_limit()
        try:
            df = pro.stk_mins(ts_code=ts_code, freq=freq,
                              start_date=cur.strftime("%Y-%m-%d 09:00:00"),
                              end_date=end.strftime("%Y-%m-%d 15:00:00"))
        except Exception as e:  # noqa: BLE001
            gaps.append({"key": "%s %s~%s" % (ts_code, cur.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
                         "reason": "%s: %s" % (type(e).__name__, str(e)[:80])})
            df = None
        if df is not None and len(df):
            byday = {}
            for _, r in df.iterrows():
                d8 = str(r["trade_time"])[:10].replace("-", "")
                byday.setdefault(d8, []).append({
                    "time": str(r["trade_time"]), "open": float(r["open"]), "close": float(r["close"]),
                    "high": float(r["high"]), "low": float(r["low"]),
                    "vol": float(r.get("vol", 0) or 0),
                    "amount": float(r["amount"]) if "amount" in df.columns else 0.0})
            for d8, bars in byday.items():
                if not keep_all and d8 not in want:
                    continue
                bars.sort(key=lambda x: x["time"])
                if len(bars) >= 40:
                    existing[d8] = bars
                    got += 1
        cur = end + timedelta(days=1)
    still = [d for d in need if len(existing.get(d) or []) < 40]
    for d in still:
        gaps.append({"key": "%s %s" % (ts_code, d), "reason": "区间模式未返回该日"})
    target.write_text(_json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    return {"fetched": got, "gaps": gaps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--pad", type=int, default=6, help="每腿取 arm_date 起 N 个交易日")
    ap.add_argument("--range", dest="use_range", action="store_true", help="区间模式（快 ~10x）")
    ap.add_argument("--keep-all", action="store_true", help="区间模式保留区间内全部日期（默认只留需要的）")
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
            if args.use_range:
                r = fetch_range(sym, days, cache, keep_all=args.keep_all)
            else:
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

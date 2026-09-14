# -*- coding: utf-8 -*-
"""fetch_m5_legs.py — 为**腿的标的**预取 m5 分钟线（用既有 tushare/brze 接口，不新造取数）。

为什么：验收 V2「破分时黄线」（狼大 2026-08-04 10:48「日均线那条黄线一旦突发跌破直接走」）
需要**标的自己的分时均价线序列**（不是大盘指数）。生产侧实时有 quote.average（t_monitor 的 vwap_break），
缺的只是回测用的历史分钟序列。

取数通道（**复用现成接口**）：backend/app/services/t_backtest_data.prefetch_m5()
  → t_data_sources._get_brze_pro() 的 stk_mins（tushare 兼容），**单线程串行 + 间隔≥1s**（卖家要求），
  → 逐日落盘到 <cache_dir>/m5/<SYMBOL>.json：{YYYYMMDD: [{time,open,close,high,low,vol,amount}]}
  → 幂等续拉（某日已有 ≥40 根即跳过），失败记 gaps（**不把失败当"没有"**）。

用法（本地跑，不要在生产容器里跑）：
  .venv/bin/python jobs/fetch_m5_legs.py --start 20260715 --end 20260911
  .venv/bin/python jobs/fetch_m5_legs.py --symbols SH515880,SZ159915 --start 20260818 --end 20260911
  # 默认符号集 = paper_trades 里策略账户（stock/t）腿涉及的标的
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "jobs"))
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_CACHE = ROOT / "data" / "m5_local"


def leg_symbols(accounts=("stock", "t")):
    """从 paper_trades 真实腿取标的（复用阶段 0 的配对逻辑）。"""
    import eval_leg_metrics as M
    rows, _meta = M.fetch_paper_trades()
    legs, _opens, _adds, _diag = M.build_legs(rows)
    return sorted({str(l["symbol_raw"] or l["symbol"]) for l in legs if l["account"] in accounts})


def trade_days(start: str, end: str):
    from app.services.t_backtest_data import resolve_trade_days
    days = [str(d)[:8] for d in (resolve_trade_days(start, end) or []) if str(d)[:8].isdigit()]
    return sorted(set(days))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔（默认为 paper_trades 腿的标的）")
    ap.add_argument("--start", default="20260715")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or leg_symbols()
    days = trade_days(args.start, args.end)
    cache = Path(args.cache)
    print("[m5] 标的 %d 只：%s" % (len(syms), ",".join(syms)))
    print("[m5] 交易日 %d 天：%s → %s" % (len(days), days[0] if days else "-", days[-1] if days else "-"))
    if not days:
        print("[m5] 没有交易日（检查 start/end 与交易日历）")
        return 2

    from app.services.t_backtest_data import prefetch_m5
    man = {"symbols": syms, "days": days, "cache": str(cache), "results": {}}
    total_gaps = []
    for i, sym in enumerate(syms, 1):
        try:
            r = prefetch_m5(sym, days, cache, is_index=False)
        except Exception as e:
            r = {"fetched": 0, "gaps": [{"key": sym, "reason": "%s: %s" % (type(e).__name__, str(e)[:80])}]}
        man["results"][sym] = {"fetched": r.get("fetched"), "gaps": len(r.get("gaps") or [])}
        total_gaps += (r.get("gaps") or [])
        print("[m5] (%d/%d) %s → 新增 %s 天，缺口 %d" % (i, len(syms), sym, r.get("fetched"), len(r.get("gaps") or [])))
    man["gaps"] = total_gaps
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[m5] 完成：%d 只，缺口 %d 条 → %s/manifest.json" % (len(syms), len(total_gaps), cache))
    if total_gaps:
        print("[m5] 缺口示例：", json.dumps(total_gaps[:5], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

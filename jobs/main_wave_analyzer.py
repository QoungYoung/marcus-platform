#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主升浪评价选股 CLI
====================
复刻「短线炒股分析员」的评价选股系统命令行入口。

用法示例:
  # 单票完整报告（Markdown）
  python jobs/main_wave_analyzer.py 600613 --as-of 20260820

  # 单票 JSON（供程序调用）
  python jobs/main_wave_analyzer.py 600613 --json --as-of 20260820

  # 多票横向对比
  python jobs/main_wave_analyzer.py 600613 002412 002081 --compare --as-of 20260820

  # 今日涨停池选股（按评分排序，取前10）
  python jobs/main_wave_analyzer.py --candidates zt --limit 10 --as-of 20260820

  # 输出到文件
  python jobs/main_wave_analyzer.py 002412 --out reports/hansen.md --as-of 20260820
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.main_wave_analyzer import (  # noqa: E402
    DataFetcher, analyze_stock, compare_stocks, score_candidates,
    _to_ts_code, _short_symbol,
)


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="主升浪五维评价选股")
    p.add_argument("symbols", nargs="*", help="股票代码，如 600613 / SH600613 / 002412.SZ")
    p.add_argument("--symbols", dest="symbols_csv", help="逗号分隔的股票代码列表")
    p.add_argument("--as-of", default=None, help="截止交易日 YYYYMMDD（默认最新交易日）")
    p.add_argument("--days", type=int, default=120, help="K线天数（默认120）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--compare", action="store_true", help="多票横向对比")
    p.add_argument("--candidates", choices=["zt", "market"], default=None,
                   help="选股来源：zt=今日涨停池（快）；market=全市场批量扫描（慢，约5-10分钟）")
    p.add_argument("--limit", type=int, default=10, help="选股数量上限")
    p.add_argument("--workers", type=int, default=4, help="批量评估并发数")
    p.add_argument("--out", default=None, help="输出到文件路径")
    p.add_argument("--verbose", action="store_true", help="打印详细日志")
    return p


def _collect_symbols(args) -> list:
    syms = list(args.symbols)
    if args.symbols_csv:
        syms += [s.strip() for s in args.symbols_csv.split(",") if s.strip()]
    return syms


def main() -> int:
    args = _argparse().parse_args()
    fetcher = DataFetcher(verbose=args.verbose)
    as_of = args.as_of

    # ── 选股模式：涨停池 ──
    if args.candidates == "zt" and not _collect_symbols(args):
        pool = fetcher.zt_pool(as_of)
        if not pool:
            print("涨停池为空（akshare 不可用或当日无数据）", file=sys.stderr)
            return 1
        # 优先取连板数高、成交额大的；去重、去 ST
        pool = [p for p in pool if "ST" not in p["name"] and p.get("pct_chg", 0) > 0]
        pool.sort(key=lambda p: (p.get("limit_times", 0), p.get("amount", 0)), reverse=True)
        symbols = [p["code"] for p in pool[:args.limit]]
        print(f"[选股] 涨停池 {len(pool)} 只，评估前 {len(symbols)} 只（连板数优先）…",
              file=sys.stderr)
        results = score_candidates(symbols, fetcher=fetcher, as_of=as_of,
                                   max_workers=args.workers, verbose=args.verbose)
        comp = compare_stocks(results)
        payload = {
            "mode": "candidates",
            "as_of": as_of,
            "pool_size": len(pool),
            "evaluated": len(symbols),
            "result": comp,
            "analyses": [
                {k: a.get(k) for k in ("symbol", "name", "industry", "score", "verdict",
                                        "structure", "five", "moneyflow", "fundamental")
                 if k in a}
                for a in results if not a.get("error")
            ],
        }
        text = _render_compare_markdown(comp)
        return _emit(payload, text, args)

    # ── 选股模式：全市场批量扫描 ──
    if args.candidates == "market":
        from core.main_wave_analyzer import market_scan
        print("[选股] 全市场批量扫描开始（该模式会全量拉取数据，请耐心等待）…",
              file=sys.stderr)
        res = market_scan(fetcher=fetcher, as_of=as_of, days=args.days,
                          top_n=args.limit, deep_n=min(8, args.limit),
                          verbose=args.verbose)
        print(f"[选股] 完成 pool={res['pool_size']} 只 | 耗时: "
              + ", ".join(f"{k}={v}s" for k, v in res["timing"].items()),
              file=sys.stderr)
        payload = {"mode": "market_scan", "as_of": as_of, "timing": res["timing"],
                   "pool_size": res["pool_size"], "ranked": res["ranked"],
                   "compare": res["compare"], "deep": res["deep"]}
        text = _render_market_scan_markdown(res)
        return _emit(payload, text, args)

    # ── 单票/多票模式 ──
    symbols = _collect_symbols(args)
    if not symbols:
        print(__doc__)
        return 2

    analyses = []
    for s in symbols:
        a = analyze_stock(s, fetcher=fetcher, as_of=as_of, days=args.days,
                          verbose=args.verbose)
        analyses.append(a)

    if args.compare:
        comp = compare_stocks(analyses)
        payload = {"mode": "compare", "as_of": as_of,
                   "result": comp,
                   "analyses": [{k: a.get(k) for k in
                                 ("symbol", "name", "industry", "score", "verdict",
                                  "structure", "five", "moneyflow", "fundamental")
                                 if k in a} for a in analyses]}
        text = _render_compare_markdown(comp)
        return _emit(payload, text, args)

    # 单票：输出完整报告
    a = analyses[0]
    if a.get("error"):
        print(json.dumps(a, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    return _emit(a, a.get("markdown", ""), args)


def _render_compare_markdown(comp: dict) -> str:
    L = ["# 主升浪候选对比", ""]
    L.append("| 排名 | 代码 | 名称 | 行业 | 阶段 | 底部涨幅 | 回撤 | 涨停数 | RSI6 | 缺口 | 天量阴线 | 趋势 | 风险 | 评分 |")
    L.append("|------|------|------|------|------|---------|------|--------|------|------|---------|------|------|------|")
    for i, r in enumerate(comp["rows"], 1):
        L.append(f"| {i} | {r['symbol']} | {r['name']} | {r['industry'] or '-'} | {r['stage']} | "
                 f"+{r['run_from_base']:.1f}% | {r['high_pullback']:+.1f}% | {r['limit_up_count']} | "
                 f"{r['rsi6']:.0f} | {r['unfilled_gaps']} | {'有' if r['has_tianliang_yin'] else '无'} | "
                 f"{r['trend']} | {r['risk']} | {r['score']} |")
    L.append("")
    for r in comp["rows"]:
        L.append(f"- **{r['name']}({r['symbol']})** [{r['grade']} {r['score']}] 阶段={r['stage']} "
                 f"风险={r['risk']} → {r['advice']}")
    return "\n".join(L)


def _render_market_scan_markdown(res: dict) -> str:
    L = ["# 全市场主升浪扫描", ""]
    L.append(f"**数据日期**: {res['as_of']} | 评估 {res['pool_size']} 只（跳过ST "
             f"{res['skipped_st']}，K线不足 {res['skipped_short']}）")
    L.append(f"**耗时**: " + ", ".join(f"{k}={v}s" for k, v in res["timing"].items()))
    L.append("")
    L.append("## Top 排名（本地快速评分）")
    L.append("| 排名 | 代码 | 名称 | 行业 | 评分 | 阶段 | 风险 | RSI6 | 底部涨幅 | 涨停 | 缺口 | 天量阴线 | 趋势 | 建议 |")
    L.append("|------|------|------|------|------|------|------|------|---------|------|------|---------|------|------|")
    for i, r in enumerate(res["ranked"], 1):
        L.append(f"| {i} | {r['symbol']} | {r['name']} | {r['industry'] or '-'} | {r['score']} | "
                 f"{r['stage']} | {r['risk']} | {r['rsi6']:.0f} | +{r['run_from_base']:.1f}% | "
                 f"{r['main_wave_lu']} | {r['unfilled_gaps']} | "
                 f"{'有' if r['has_tianliang_yin'] else '无'} | {r['trend']} | {r['advice']} |")
    L.append("")
    comp = res.get("compare", {})
    if comp.get("rows"):
        L.append("## Top 深度分析对比")
        L.append("| 排名 | 代码 | 名称 | 阶段 | 风险 | 评分 | 建议 |")
        L.append("|------|------|------|------|------|------|------|")
        for i, row in enumerate(comp["rows"], 1):
            L.append(f"| {i} | {row['symbol']} | {row['name']} | {row['stage']} | {row['risk']} | "
                     f"{row['score']} | {row['advice']} |")
    return "\n".join(L)


def _emit(payload: dict, text: str, args) -> int:
    if args.json:
        out = json.dumps(payload if "result" in payload else payload,
                         ensure_ascii=False, indent=2, default=str)
    else:
        out = text
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"已写入 {args.out}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

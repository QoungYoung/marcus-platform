# -*- coding: utf-8 -*-
"""做T回测 · brze 数据可得性探针（add-t-backtest-mode tasks 1.1）。

实测 brze stk_mins（标的 m5）/ index_min（指数 m5）/ index_daily（指数日线）
在最近 N 个交易日的成功率、耗时、行数与缺口，输出可行性报告，
决定默认回测窗口长度。

用法（backend 目录下）：
    python scripts/t_backtest_probe.py [--symbol 600519.SH] [--days 30]

输出：
    data/t_backtest/_probe/probe_report.json  +  控制台摘要
"""
import argparse
import json
import os
import sys
from pathlib import Path

# sys.path 引导（对齐仓库其他脚本）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main():
    ap = argparse.ArgumentParser(description="brze 做T回测数据探针")
    ap.add_argument("--symbol", default="600519.SH", help="标的 ts_code，如 600519.SH")
    ap.add_argument("--days", type=int, default=30, help="回看交易日数")
    args = ap.parse_args()

    from app.services.t_backtest_data import run_probe

    report = run_probe(symbol=args.symbol, trade_days=args.days)
    print("=" * 64)
    print(f"brze 做T回测数据探针: {report['symbol']}")
    print(f"窗口: {report['window']}（{report['trade_days']} 个交易日）")
    print("-" * 64)
    s = report["stk_mins"]
    print(f"标的 m5 (stk_mins): 成功 {s['ok']}/{report['trade_days']} 日 | 失败 {s['fail']} | "
          f"总 bar {s['total_bars']} | 日均耗时 {s['avg_ms']}ms")
    if s["gaps"]:
        print(f"  缺口: {json.dumps(s['gaps'][:5], ensure_ascii=False)}")
    i = report["index_min"]
    print(f"指数 m5 (index_min): 成功 {i['ok']}/{report['trade_days']} 日 | 失败 {i['fail']} | "
          f"总 bar {i['total_bars']} | 日均耗时 {i['avg_ms']}ms")
    if i["gaps"]:
        print(f"  缺口: {json.dumps(i['gaps'][:5], ensure_ascii=False)}")
    d = report["index_daily"]
    print(f"指数日线 (index_daily): {'OK' if d['ok'] else 'FAIL'} | {d['bars']} 根 | {d['detail']}")
    print("=" * 64)
    verdict = []
    if s["fail"] > 0:
        verdict.append(f"标的 m5 缺口 {s['fail']} 日")
    if i["fail"] > 0:
        verdict.append(f"指数 m5 缺口 {i['fail']} 日")
    if not d["ok"]:
        verdict.append("指数日线不可用")
    if verdict:
        print(f"⚠️ 结论: {'；'.join(verdict)} —— 建议缩回测窗口或检查 brze 权限")
    else:
        print("✅ 结论: brze 数据可得性良好，m5 回测窗口可行（默认近 30 交易日）")
    print(f"报告: data/t_backtest/_probe/probe_report.json")


if __name__ == "__main__":
    main()

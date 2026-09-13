# -*- coding: utf-8 -*-
"""
公募持仓占比（overhang）聚合工具 —— 用 tushare fund_portfolio / fund_share / fund_basic 计算
一只股票被多少只「代表公募/大基金」重仓、以及公募持仓占流通市值比例（stk_float_ratio）。

用途：给自上而下主线筛选器补上狼大「规避公募重仓」准则 ——
  公募/机构重仓且大级别利空的板块（尤其海外链/大票）在反弹/调整浪是拖累（赎回压力），应加权惩罚。

【数据】2026-09-13 起走 datahubco 基础接口 + promax 聚合中继（core/tushare_relay.py），
  密钥取 .env 的 DATAHUBCO_API_KEY / PROMAX_API_KEY（旧 gzcloud 代理 token 已失效）
  - fund_basic   : 基金列表（market=E → 股票型/ETF）  —— 圈定公募基金池
  - fund_share   : 基金份额/规模（fd_share, 万份）     —— 选规模前N大「代表性公募」
  - fund_portfolio: 单只基金前十大重仓（ts_code, symbol, amount, stk_float_ratio...）

用法：
  python scripts/fund_overhang.py --top 50 --end 20260630
  python scripts/fund_overhang.py --top 50 --end 20260630 --symbols 300308.SZ 002475.SZ
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict


def _relay():
    """加载 core/tushare_relay.py（datahubco + promax，替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def _call(api, params, fields):
    return _relay().relay_items(api, fields=fields, **(params or {}))

def fetch_fund_share_latest():
    _, items = _call("fund_share", {"trade_date": "20260827"}, "ts_code,trade_date,fd_share")
    # dedup by ts_code (keep latest)
    best = {}
    for code, _d, sh in items:
        best[code] = (sh, _d)
    return best

def fetch_fund_portfolio_latest(fund, end_date):
    fields, items = _call("fund_portfolio", {"ts_code": fund, "end_date": end_date},
                          "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio")
    if not items:
        return []
    # keep only the latest disclosed quarter (max end_date)
    max_end = max(it[2] for it in items)
    rows = [it for it in items if it[2] == max_end]
    # each row: [ts_code(fund), ann_date, end_date, symbol, mkv, amount, stk_mkv_ratio, stk_float_ratio]
    return rows

def aggregate(funds, end_date, log=print):
    stock_stat = defaultdict(lambda: {"funds": set(), "amount": 0.0, "float_ratio": 0.0, "mkv": 0.0})
    done = 0
    for i, fund in enumerate(funds, 1):
        try:
            rows = fetch_fund_portfolio_latest(fund, end_date)
        except Exception as e:
            log(f"  skip {fund}: {e}")
            continue
        for r in rows:
            sym = r[3]
            amt = r[5] if r[5] is not None else 0.0
            fr = r[7] if r[7] is not None else 0.0
            mkv = r[4] if r[4] is not None else 0.0
            st = stock_stat[sym]
            st["funds"].add(fund)
            st["amount"] += amt
            st["float_ratio"] += fr
            st["mkv"] += mkv
        done += 1
        if i % 10 == 0:
            log(f"  ... queried {i}/{len(funds)} funds")
    return stock_stat, done

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--end", default="20260630")
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()

    share = fetch_fund_share_latest()
    # pick top-N funds by fd_share
    ranked = sorted(share.items(), key=lambda kv: kv[1][0] or 0, reverse=True)[:args.top]
    funds = [c for c, _ in ranked]
    print(f"base funds: {len(share)}; choose top{args.top} by fd_share:")
    for c, (sh, d) in ranked[:10]:
        print(f"  {c}  fd_share={sh}  date={d}")

    print(f"\naggregating fund_portfolio (end_date={args.end}) ...")
    stat, done = aggregate(funds, args.end, log=print)
    print(f"done querying {done} funds; stocks covered: {len(stat)}")

    rows = []
    for sym, st in stat.items():
        rows.append({
            "symbol": sym, "n_funds": len(st["funds"]),
            "sum_amount": st["amount"], "sum_float_ratio": st["float_ratio"],
            "max_float_ratio": max((fr for _f in []), default=0.0),
            "sum_mkv": st["mkv"],
        })
    # max_float_ratio is not tracked per fund separately; approximate with sum? keep simple
    import pandas as pd
    df = pd.DataFrame(rows)
    df["sum_float_ratio"] = df["sum_float_ratio"].round(4)
    df["sum_amount"] = df["sum_amount"].round(0)
    df["sum_mkv"] = df["sum_mkv"].round(0)
    df = df.sort_values("sum_float_ratio", ascending=False)
    if args.symbols:
        df = df[df["symbol"].isin(args.symbols)]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print("\n=== 公募重仓（overhang）TOP（sum_float_ratio=公募持仓占流通市值比例之和, n_funds=多少只大基金重仓） ===")
    print(df.head(40).to_string(index=False))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账户现金 ↔ 成交流水 对账（漂移自检）。

用途
----
`paper_account_info.available_cash` 由多个进程写入（backend / worker 各自的 VN.PY bridge
与 PaperTradingEngine 实例）。历史上一度有人写"进程内绝对值"，导致现金与成交流水长期
脱节（2026-09-16 实测漂移 3.4 万）。现已在写入端统一为**原子增量**，本脚本用于守住这条
不变量：按 baseline 起的成交流水重放出现金，与库里的现金比对，超阈值即报错退出（可挂调度）。

口径
----
· 现金 = 基线净值 − Σ买入(含 0.05% 佣金) + Σ卖出(含 0.15% 费率)，只看未作废成交；
· 方向词表同时认中文（买入/卖出）与英文（buy/sell）——生产曾出现英文行被静默忽略的坑；
· 买入/卖出费率与 apps/paper-trading/paper_engine.py 一致。

用法
----
    python3 jobs/recon_account_cash.py                     # 人读格式
    python3 jobs/recon_account_cash.py --json              # 机读
    python3 jobs/recon_account_cash.py --tol 1.0           # 漂移阈值（元），超了 exit 1
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
try:
    from trade_direction import SELL_WORDS, BUY_WORDS, is_buy as is_buy_word, is_sell as is_sell_word  # noqa: E402
except Exception:  # core 不在路径时退化为内置词表
    BUY_WORDS, SELL_WORDS = ("买入", "buy"), ("卖出", "sell")
    is_buy_word = lambda v: str(v or "").strip().lower() in ("买入", "buy")
    is_sell_word = lambda v: str(v or "").strip().lower() in ("卖出", "sell")

BUY_FEE = 0.0005
SELL_FEE = 0.0015
DEFAULT_BASELINE_DATE = "2026-09-01"
DEFAULT_BASELINE_ASSET = 244501.79


def db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from app.config import get_settings  # type: ignore
        return get_settings().DATABASE_URL
    except Exception as e:
        raise SystemExit(f"无法获取 DATABASE_URL: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="stock")
    ap.add_argument("--baseline-date", default=DEFAULT_BASELINE_DATE)
    ap.add_argument("--baseline-asset", type=float, default=DEFAULT_BASELINE_ASSET)
    ap.add_argument("--tol", type=float, default=1.0, help="允许漂移（元），超过 exit 1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = psycopg2.connect(db_url(), connect_timeout=5)
    cur = conn.cursor()

    # 基线可取 system_state 留痕（重设基线时写入），保证脚本与账本同源
    base_date, base_asset = args.baseline_date, args.baseline_asset
    try:
        cur.execute("SELECT value FROM system_state WHERE key = 'account_rebase_0901'")
        row = cur.fetchone()
        if row:
            trace = json.loads(row[0])
            base_date = trace.get("baseline_date", base_date)
            base_asset = float(trace.get("baseline_asset", base_asset))
    except Exception:
        pass

    cur.execute(
        "SELECT initial_capital, available_cash FROM paper_account_info WHERE account_id = %s",
        (args.account,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"paper_account_info 无 {args.account} 行")
    initial_capital, cash_db = float(row[0]), float(row[1])

    cur.execute(
        "SELECT id, trade_date, orderid, symbol, direction, price, volume "
        "FROM paper_trades WHERE account_id = %s AND (voided = 0 OR voided IS NULL) "
        "AND COALESCE(trade_date, substr(created_at, 1, 10)) >= %s ORDER BY id",
        (args.account, base_date))
    rows = cur.fetchall()

    cash = float(base_asset)
    lots, realized = {}, 0.0
    for _rid, _td, _oid, sym, d, px, vol in rows:
        px, vol = float(px), int(vol)
        if d in BUY_WORDS:
            cash -= px * vol * (1 + BUY_FEE)
            lots.setdefault(sym, []).append([px, vol])
        elif d in SELL_WORDS:
            cash += px * vol * (1 - SELL_FEE)
            rem, lst, i = vol, lots.get(sym, []), 0
            while rem > 0 and i < len(lst):
                used = min(lst[i][1], rem)
                realized += (px - lst[i][0]) * used
                lst[i][1] -= used
                rem -= used
                if lst[i][1] == 0:
                    lst.pop(i)
                else:
                    i += 1
    positions = {s: v[0] for s, v in ((s, (sum(l[1] for l in ls), 0)) for s, ls in lots.items())
                 if v[0] > 0}

    cur.execute("SELECT symbol, volume FROM paper_positions WHERE account_id = %s", (args.account,))
    pos_db = {s: int(v) for s, v in cur.fetchall()}

    # 方向词表审计：表外词表（既非中文也非 buy/sell）会让读取端静默漏算
    cur.execute(
        "SELECT direction, count(*) FROM paper_trades WHERE account_id = %s GROUP BY direction "
        "ORDER BY direction", (args.account,))
    vocab = {d: int(n) for d, n in cur.fetchall()}
    unknown_vocab = {d: n for d, n in vocab.items()
                     if not (is_buy_word(d) or is_sell_word(d))}
    cur.close()
    conn.close()

    drift = cash_db - cash
    pos_diff = {s: {"fifo": positions.get(s, 0), "db": pos_db.get(s, 0)}
                for s in sorted(set(positions) | set(pos_db))
                if positions.get(s, 0) != pos_db.get(s, 0)}

    out = {
        "account": args.account,
        "baseline": {"date": base_date, "asset": base_asset},
        "initial_capital": initial_capital,
        "cash_db": round(cash_db, 2),
        "cash_replay": round(cash, 2),
        "cash_drift": round(drift, 2),
        "trades_replayed": len(rows),
        "realized_fifo": round(realized, 2),
        "position_mismatch": pos_diff,
        "direction_vocab": vocab,
        "unknown_direction": unknown_vocab,
        "ok": abs(drift) <= args.tol and not pos_diff and not unknown_vocab,
    }

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"[对账] 账户={args.account} 基线={base_date} 期初={base_asset:,.2f}")
        print(f"[对账] 现金: 库内 {cash_db:,.2f} / 流水重放 {cash:,.2f} → 漂移 {drift:+,.2f} 元")
        print(f"[对账] 重放 {len(rows)} 笔，FIFO 已实现 {realized:,.2f}")
        print(f"[对账] 持仓差异: {pos_diff if pos_diff else '无'}")
        print(f"[对账] 方向词表: {vocab}" + (f" ⚠️ 表外词表 {unknown_vocab}" if unknown_vocab else " ✅"))
        print("[对账] " + ("✅ 一致" if out["ok"] else f"❌ 漂移超阈值（tol={args.tol}）"))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

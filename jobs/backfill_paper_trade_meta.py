#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补齐 paper_trades 的 reason / profit（历史欠账回填）。

背景
----
`paper_trades` 有两条写入路径，口径不一致：
  · engine 路径（apps/paper-trading/paper_engine.py）：reason + FIFO profit 都写全；
  · VN.PY 桥接路径（backend/app/core/trading/vnpy_listeners.py 的 TradeEventListener）：
    只写 orderid/价格/数量，**profit 恒 0、reason 恒空**。
而 `paper_trades.profit` 是当日已实现盈亏的**权威口径**（t_gateway._realized_today()、
wolf_profit_cushion 利润垫），空值会让统计系统性低估。

`data/marcus_trades.jsonl` 由执行器 MarcusVNPyExecutor 写入，含完整的 order_id /
reason / profit，因此可以作为回填来源。本脚本按 orderid 匹配 UPDATE，**只补空值**，
不覆盖已写入的非空 reason / 非零 profit，也从不 INSERT（监听器仍是唯一写入方）。

用法
----
    python3 jobs/backfill_paper_trade_meta.py                # 默认 dry-run，只打印
    python3 jobs/backfill_paper_trade_meta.py --apply        # 真正写库
    python3 jobs/backfill_paper_trade_meta.py --since 2026-09-01 --apply
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2


def _find_jsonl() -> Path:
    """定位 data/marcus_trades.jsonl（容器内 /app/data，本机从脚本位置向上找仓库根）。"""
    env_dir = os.getenv("MARCUS_DATA_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir) / "marcus_trades.jsonl")
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidates.append(parent / "data" / "marcus_trades.jsonl")
        if (parent / "AGENTS.md").exists() or (parent / ".git").exists():
            break
    candidates.append(Path("/app/data/marcus_trades.jsonl"))
    for c in candidates:
        if c.exists():
            return c
    raise SystemExit(f"找不到 marcus_trades.jsonl，尝试过: {[str(c) for c in candidates]}")


def _db_url() -> str:
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
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--since", default="", help="只回填该日期(含)之后的成交，YYYY-MM-DD")
    ap.add_argument("--account", default="stock", help="账户，默认 stock")
    ap.add_argument("--file", default="", help="指定 marcus_trades.jsonl 路径")
    args = ap.parse_args()

    jsonl = Path(args.file) if args.file else _find_jsonl()
    print(f"[回填] 数据源: {jsonl}  模式: {'APPLY' if args.apply else 'DRY-RUN'}")

    records = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("status") != "executed" or rec.get("type") not in ("buy", "sell"):
            continue
        oid = str(rec.get("order_id") or "")
        if not oid.startswith("PAPER."):
            continue  # 只处理桥接路径（engine 路径本来就写全）
        ts = str(rec.get("timestamp") or "")
        if args.since and ts[:10] < args.since:
            continue
        records.append(rec)

    print(f"[回填] 桥接成交记录 {len(records)} 条")

    conn = psycopg2.connect(_db_url(), connect_timeout=5)
    # 注意：dry-run 也必须 autocommit=False —— autocommit=True 时每条 UPDATE 立即提交，
    # 末尾的 rollback() 是空操作，"dry-run" 会真的写库（首版即踩此坑）。
    conn.autocommit = False
    cur = conn.cursor()

    stats = {"hit": 0, "no_row": 0, "no_change": 0}
    for rec in records:
        oid = str(rec["order_id"])
        bare = oid.split(".", 1)[1]
        direction = "买入" if rec["type"] == "buy" else "卖出"
        symbol = rec.get("symbol", "")
        price = float(rec.get("price") or 0)
        volume = int(rec.get("volume") or 0)
        reason = (rec.get("reason") or "").strip()
        profit = rec.get("profit") if direction == "卖出" else None

        set_parts = ["reason = CASE WHEN COALESCE(reason, '') = '' THEN %s ELSE reason END"]
        params = [reason]
        # 只匹配「确实缺值」的行 —— Postgres 的 rowcount 统计的是 WHERE 命中行数，
        # 不收紧 WHERE 的话第二次运行仍会报「补写 N 行」，读不出是否幂等。
        need_fix = ["COALESCE(reason, '') = ''"]
        if profit is not None:
            set_parts.append("profit = CASE WHEN COALESCE(profit, 0) = 0 THEN %s ELSE profit END")
            params.append(round(float(profit), 4))
            need_fix.append("COALESCE(profit, 0) = 0")
        params += [oid, bare, args.account, symbol,
                   ['买入', 'buy'] if direction == "买入" else ['卖出', 'sell'],
                   price, volume]

        cur.execute(
            "UPDATE paper_trades SET " + ", ".join(set_parts) +
            " WHERE orderid IN (%s, %s) AND account_id = %s AND symbol = %s "
            "AND direction = ANY(%s) AND price = %s AND volume = %s "
            "AND (" + " OR ".join(need_fix) + ")",
            tuple(params),
        )
        if cur.rowcount:
            stats["hit"] += 1
            print(f"  ✓ {rec['timestamp'][:19]} {direction} {symbol} {volume}@{price} "
                  f"profit={profit if profit is None else round(float(profit), 2)}")
        else:
            # 行存在但两列都已有值 → 无变化；行不存在 → 监听器没写（异常）
            cur.execute(
                "SELECT count(*) FROM paper_trades WHERE orderid IN (%s, %s) AND account_id = %s",
                (oid, bare, args.account),
            )
            exists = cur.fetchone()[0]
            if exists:
                stats["no_change"] += 1
            else:
                stats["no_row"] += 1
                print(f"  ! 无对应行: {oid} {symbol}")

    if args.apply:
        conn.commit()
    else:
        conn.rollback()

    cur.close()
    conn.close()
    print(f"[回填] 完成: 补写 {stats['hit']} / 已有值无需改 {stats['no_change']} / 无行 {stats['no_row']}")
    if not args.apply:
        print("[回填] DRY-RUN 未写库，确认无误后加 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""账户回滚（账本 §9.742）—— 把某账户回滚到"某日收盘"，以便从次日起重跑

为什么需要 ✗：
  `RESUME_FROM=<day>` 的语义是「**不 reset 账户**、复用沙箱」✓（用于**续跑未完成的天**）
  但若要**重跑已跑过的天**（例如口径修好后重算 ✓），账户里已经含了那些天的成交 ✗
  ⇒ 直接重跑会**重复计账** ✗ ⇒ 必须先回滚到起点前一日收盘 ✓

回滚依据 ✓：`<root>/_summary/prod_<to>.json` 里的账户/持仓快照 ✓
  · `account[0].available_cash` / `order_counter` ✓
  · `positions[]`（symbol/volume/avg_price/entry_date ✓）
  · ★ 快照**没有** `highest_price` ✗ ⇒ 用 `bars_adj` 从 entry_date 到 `--to` **重算** ✓
    （口径与 `WOLF_ADJ_PRICE=1` 同尺 ✓；实测与跑批自维护值**逐位一致** ✓）

做五件事 ✓（全部在**一个事务**里 ✓）：
  ① 备份将被删的行到 `<tmp>/rollback_<from>_backup.json` ✓
  ② 删 `paper_trades` / `paper_orders`（本账户，日期 >= from ✓）
  ③ 重建 `paper_positions`（按 `--to` 快照 ＋ 重算 highest ✓）
  ④ 恢复 `paper_account_info`（现金/冻结/单号计数器 ✓）
  ⑤ 清 `t_conditions` / `t_ai_actions`（trade_date >= from ✓）
     ＋ `t_triggers`（无 trade_date 列 ✗ ⇒ 用 `snapshot->>'quote_time'` 定位 ✓）

安全 ✓：默认 **dry-run**（只打印 ✓）；要真做必须加 `--yes` ✓。

用法 ✓：
  .venv/bin/python jobs/bt_rollback_account.py --to 20260115 --from 20260116
  .venv/bin/python jobs/bt_rollback_account.py --to 20260115 --from 20260116 --yes
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

DSN_DEFAULT = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"


def ts_code_of(sym: str) -> str:
    """`SH688615` ⇒ `688615.SH`（bars 表的 ts_code 形态 ✓）"""
    return sym[2:] + "." + sym[:2]


def dash8(d8: str) -> str:
    return "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8]) if len(d8) == 8 else d8


def highest_since(bars_db: str, sym: str, entry8: str, last8: str) -> Tuple[float, int]:
    """建仓日 ~ last8 的**最高价**（复权 ✓）；返回 (最高价, 命中 bar 数) ✓

    ⚠️ `bars.trade_date` 是**紧凑 8 位**（`20260105` ✓）—— 传带横杠的日期会**查不到** ✗
       （本次就因这个先算错一轮 ✓ ⇒ 参数一律先 `.replace('-','')` ✓）
    """
    try:
        con = sqlite3.connect("file:%s?mode=ro" % bars_db, uri=True)
        row = con.execute(
            "SELECT count(*), max(high) FROM bars WHERE ts_code=? AND trade_date>=? AND trade_date<=?",
            (ts_code_of(sym), entry8.replace("-", ""), last8.replace("-", ""))).fetchone()
        con.close()
        n = int(row[0] or 0)
        return (float(row[1]) if row[1] is not None else 0.0), n
    except Exception as exc:  # noqa: BLE001
        print("    （读 bars 失败 %s: %s）" % (sym, str(exc)[:60]))
        return 0.0, 0


def main() -> int:
    ap = argparse.ArgumentParser(description="账户回滚到某日收盘（默认 dry-run ✓）")
    ap.add_argument("--to", required=True, help="回滚到这天**收盘**（8 位，如 20260115 ✓）")
    ap.add_argument("--from", dest="frm", required=True, help="重跑起点（删 >= 该日 ✓，通常 to 的次日 ✓）")
    ap.add_argument("--account", default="drabt35d")
    ap.add_argument("--root", default="data/_bt_t35d")
    ap.add_argument("--bars", default="data/_bt_full/bars_adj.sqlite")
    ap.add_argument("--dsn", default=os.getenv("DATABASE_URL") or DSN_DEFAULT)
    ap.add_argument("--yes", action="store_true", help="真正执行（默认只打印 ✓）")
    a = ap.parse_args()

    snap_path = os.path.join(a.root, "_summary", "prod_%s.json" % a.to)
    if not os.path.exists(snap_path):
        print("  ✗ 找不到快照 %s（该日尚未跑完？⇒ 应回滚到**已完成的最后一天** ✓）" % snap_path)
        return 2
    snap = json.load(open(snap_path, encoding="utf-8"))
    acc = (snap.get("account") or [{}])[0]
    poss: List[Dict[str, Any]] = snap.get("positions") or []
    if not acc or not poss:
        print("  ✗ 快照缺 account/positions：%s" % snap_path)
        return 2

    print("  快照 ✓ %s" % snap_path)
    print("    现金=%s ｜ counter=%s ｜ 持仓 %d 只" % (acc.get("available_cash"), acc.get("order_counter"), len(poss)))
    hi: Dict[str, Tuple[float, int]] = {}
    for p in poss:
        h, n = highest_since(a.bars, p["symbol"], str(p["entry_date"]), a.to)
        hi[p["symbol"]] = (round(h, 4) if n else float(p["avg_price"]), n)
    print("    重算 highest ✓ %s" % json.dumps({k: v[0] for k, v in hi.items()}, ensure_ascii=False))

    try:
        import psycopg2
        import psycopg2.extras
    except Exception as exc:  # noqa: BLE001
        print("  ✗ 缺 psycopg2：%s" % exc)
        return 2

    conn = psycopg2.connect(a.dsn, connect_timeout=8)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def count(sql: str, args: tuple) -> int:
        cur.execute(sql, args)
        return int((cur.fetchone() or {}).get("n") or 0)

    n_tr = count("SELECT count(*) n FROM paper_trades WHERE account_id=%s AND trade_date >= %s", (a.account, dash8(a.frm)))
    n_or = count("SELECT count(*) n FROM paper_orders WHERE account_id=%s AND created_at >= %s", (a.account, dash8(a.frm)))
    n_po = count("SELECT count(*) n FROM paper_positions WHERE account_id=%s", (a.account,))
    n_cd = count("SELECT count(*) n FROM t_conditions WHERE account_id=%s AND trade_date >= %s", (a.account, dash8(a.frm)))
    n_ai = count("SELECT count(*) n FROM t_ai_actions WHERE trade_date >= %s", (dash8(a.frm),))
    n_tg = count("SELECT count(*) n FROM t_triggers WHERE account_id=%s AND COALESCE(snapshot->>'quote_time','') >= %s",
                 (a.account, dash8(a.frm)))
    print("  ── 将处理（回滚到 %s 收盘；重跑起点 %s）:" % (a.to, a.frm))
    print("     paper_trades(>=%s)   ⇒ %d 行" % (a.frm, n_tr))
    print("     paper_orders(>=%s)   ⇒ %d 行" % (a.frm, n_or))
    print("     paper_positions      ⇒ 全部替换（%d 行 ⇒ %d 行 ✓）" % (n_po, len(poss)))
    print("     t_conditions(>=%s)   ⇒ %d 行" % (a.frm, n_cd))
    print("     t_ai_actions(>=%s)   ⇒ %d 行" % (a.frm, n_ai))
    print("     t_triggers(>=%s)     ⇒ %d 行" % (a.frm, n_tg))
    if not a.yes:
        print("  （dry-run ✓ 未改动任何数据；要执行请加 --yes ✓）")
        conn.rollback()
        conn.close()
        return 0

    backup: Dict[str, Any] = {"_meta": {"to": a.to, "frm": a.frm, "account": a.account}}
    for key, sql, args in (
        ("trades_del", "SELECT * FROM paper_trades WHERE account_id=%s AND trade_date >= %s", (a.account, dash8(a.frm))),
        ("orders_del", "SELECT * FROM paper_orders WHERE account_id=%s AND created_at >= %s", (a.account, dash8(a.frm))),
        ("positions_before", "SELECT * FROM paper_positions WHERE account_id=%s", (a.account,)),
        ("account_before", "SELECT * FROM paper_account_info WHERE account_id=%s", (a.account,)),
    ):
        cur.execute(sql, args)
        backup[key] = [dict(r) for r in cur.fetchall()]

    cur.execute("DELETE FROM paper_trades WHERE account_id=%s AND trade_date >= %s", (a.account, dash8(a.frm)))
    cur.execute("DELETE FROM paper_orders WHERE account_id=%s AND created_at >= %s", (a.account, dash8(a.frm)))
    cur.execute("DELETE FROM paper_positions WHERE account_id=%s", (a.account,))
    for p in poss:
        cur.execute(
            """INSERT INTO paper_positions (symbol, entry_date, highest_price, updated_at, volume, frozen, avg_price, account_id)
               VALUES (%s,%s,%s,now(),%s,0,%s,%s)""",
            (p["symbol"], str(p["entry_date"])[:10], hi.get(p["symbol"], (float(p["avg_price"]), 0))[0],
             int(p["volume"]), float(p["avg_price"]), a.account))
    cur.execute("""UPDATE paper_account_info SET available_cash=%s, frozen_cash=0, order_counter=%s, updated_at=now()
                   WHERE account_id=%s""",
                (float(acc["available_cash"]), int(acc["order_counter"]), a.account))
    cur.execute("DELETE FROM t_conditions WHERE account_id=%s AND trade_date >= %s", (a.account, dash8(a.frm)))
    cur.execute("DELETE FROM t_ai_actions WHERE trade_date >= %s", (dash8(a.frm),))
    cur.execute("""DELETE FROM t_triggers WHERE account_id=%s AND COALESCE(snapshot->>'quote_time','') >= %s""",
                (a.account, dash8(a.frm)))
    conn.commit()

    out = os.path.join(".dsh-tmp", "wolfbt", "rollback_%s_backup.json" % a.frm)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(backup, open(out, "w", encoding="utf-8"), ensure_ascii=False, default=str)
    conn.close()
    print("  ✅ 回滚完成 ✓（备份 ⇒ %s ✓）" % out)
    print("     ⇒ 现在可 `RESUME_FROM=%s bash <launcher>` 重跑 ✓" % a.frm)
    return 0


if __name__ == "__main__":
    sys.exit(main())

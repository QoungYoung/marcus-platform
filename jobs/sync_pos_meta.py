# -*- coding: utf-8 -*-
"""sync_pos_meta.py —— 把持仓的 `highest_price`／**转正**状态同步进**臂库**（账本 §9.533 ✓）

**为什么要它** ✓（用户原话：「数据落库…每个臂的数据都可以查」✓）：
  · 「转正」＝ `highest_price ≥ 成本×(1+WOLF_AMBUSH_PROMOTE_PCT%)`（pins 10 ✓）
  · 但它**靠一个字段**判定 ⇒ 一旦该字段被写坏（重置/重标 ✗）⇒ **永远不转正** ⇒ 不止盈 ✗
  · ⇒ ⇒ 把「**最高价／是否转正／转正价**」**落到臂库** ⇒ 以后一条 SQL 就能查「为什么没转正」✓
**无前视** ✓：真实最高只取 `entry_date ≤ 交易日 ≤ --upto` ✓
用法：`.venv/bin/python jobs/sync_pos_meta.py --account drabt35 [--upto 20260428] [--fix-pg]`
"""
from __future__ import annotations
import os, sqlite3, sys


def conv(s: str) -> str:
    s = str(s).strip().upper()
    return (s[2:] + "." + s[:2]) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s


def main() -> int:
    acc, upto, fix_pg = "drabt35", "20260428", False
    for i, a in enumerate(sys.argv):
        if a == "--account" and i + 1 < len(sys.argv): acc = sys.argv[i + 1]
        if a == "--upto" and i + 1 < len(sys.argv): upto = sys.argv[i + 1]
        if a == "--fix-pg": fix_pg = True
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pct = float(os.getenv("WOLF_AMBUSH_PROMOTE_PCT", "10") or 10)
    cc = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % repo, uri=True)
    cc.execute("PRAGMA temp_store=MEMORY")
    import psycopg2
    dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
    cn = psycopg2.connect(dsn, connect_timeout=4)
    cn.autocommit = True
    cur = cn.cursor()
    cur.execute("SELECT symbol, avg_price, highest_price, entry_date FROM paper_positions WHERE account_id=%s", (acc,))
    rows = cur.fetchall()
    sys.path.insert(0, os.path.join(repo, "jobs"))
    import arm_db as adb
    c = adb.connect(acc)
    n_promo = 0
    print("  ── 转正核对（阈值 %g%% ✓）──" % pct)
    for sym, ap, hp, ed in rows:
        d0 = str(ed).replace("-", "")[:8]
        r = list(cc.execute("SELECT max(close) FROM bars WHERE ts_code=? AND trade_date>=? AND trade_date<=?",
                            (conv(sym), d0, upto)))
        real = float(r[0][0]) if r and r[0][0] else 0.0
        promo = bool(real > 0 and real >= float(ap or 0) * (1 + pct / 100.0))
        if promo:
            n_promo += 1
        adb.put_pos_meta(c, acc, sym, d0, float(ap or 0), real, promo,
                         (d0 if promo else ""), real if promo else 0.0)
        print("    %-9s 成本%7.2f 记录最高%7.2f ⇒ **真实最高%7.2f（%+6.1f%%）⇒ 转正=%s**"
              % (sym, float(ap or 0), float(hp or 0), real, (real / float(ap or 0) - 1) * 100 if ap else 0,
                 "**是 ✓**" if promo else "否 ✗"))
        if fix_pg and real > 0 and abs(real - float(hp or 0)) > 0.005:
            cur.execute("UPDATE paper_positions SET highest_price=%s, updated_at=now() WHERE account_id=%s AND symbol=%s",
                        (real, acc, sym))
    print("  ⇒ 应转正 **%d / %d** 只 ✓%s" % (n_promo, len(rows), "（已同步 PG ✓）" if fix_pg else "（仅同步臂库 ✓）"))
    for r in c.execute("SELECT symbol, avg_price, highest_price, promoted FROM pos_meta WHERE promoted=1 ORDER BY symbol"):
        print("    ✓ 已转正:", r)
    c.close()
    cn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

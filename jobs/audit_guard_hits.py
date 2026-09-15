# -*- coding: utf-8 -*-
"""audit_guard_hits.py — 生产里「哪些护栏真的在咬」+ 重试放大订正（总账 §48，round 36）。

用途：回答"某个自设护栏（日上限/时段禁/冷静期…）在生产到底挡了多少"。
⚠️ 关键坑：`t_build_events` 会被**重试风暴**放大 —— 实测 2026-08-15→08-29 窗口 498 条，
按 (日期, 标的) 去重后**只有 14 个**（同一标的每分钟重试一次）。**任何统计都必须先按 (日期,标的) 去重**，
否则会把"3 个标的重试 65 次"读成"200 次被拒"。

用法::

    # 本地（SSH 隧道）
    .venv/bin/python jobs/audit_guard_hits.py
    # 容器内
    docker exec marcus-worker python /app/jobs/audit_guard_hits.py
    # 只看某个日期范围
    .venv/bin/python jobs/audit_guard_hits.py --since 2026-08-15 --until 2026-08-29
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def connect():
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        import psycopg2
        return psycopg2.connect(dsn)
    helper = os.path.join(REPO, ".dsh-tmp", "wolfbt")
    if os.path.isdir(helper):
        sys.path.insert(0, helper)
        from local_pg import ensure_tunnel, DSN
        ensure_tunnel()
        import psycopg2
        return psycopg2.connect(**DSN)
    raise SystemExit("需要 DATABASE_URL，或本地存在的 .dsh-tmp/wolfbt/local_pg.py（SSH 隧道）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD（默认全窗口）")
    ap.add_argument("--until", default="", help="结束日期 YYYY-MM-DD")
    args = ap.parse_args()

    where = []
    if args.since:
        where.append("created_at::date >= '%s'" % args.since)
    if args.until:
        where.append("created_at::date <= '%s'" % args.until)
    cond = (" WHERE " + " AND ".join(where)) if where else ""

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT min(created_at)::date, max(created_at)::date, count(*) FROM t_build_events" + cond)
    lo, hi, n = cur.fetchone()
    print("== 建仓尝试（t_build_events）窗口 %s → %s，原始 %d 条" % (lo, hi, n))
    cur.execute("SELECT count(DISTINCT (created_at::date, symbol)) FROM t_build_events" + cond)
    dedup = cur.fetchone()[0]
    print("   按 (日期,标的) 去重后：**%d** 个 —— 放大倍数 %.1f×（重试风暴，统计必须去重）"
          % (dedup, (n / dedup) if dedup else 0))

    print("\n   %-38s %8s %14s" % ("拦截原因", "原始条数", "去重(日,标的)"))
    w = (cond + " AND status <> 'executed'") if cond else " WHERE status <> 'executed'"
    cur.execute("""SELECT left(coalesce(reason,''),36) r, count(*) a,
                          count(DISTINCT (created_at::date, symbol)) b
                   FROM t_build_events%s GROUP BY 1 ORDER BY 2 DESC LIMIT 12""" % w)
    for r, a, b in cur.fetchall():
        print("   %-38s %8s %14s" % (r, a, b))

    cur.execute("SELECT status, count(*) FROM t_build_events%s GROUP BY 1" % cond)
    print("\n   状态分布:", cur.fetchall())

    cur.execute("SELECT min(created_at)::date, max(created_at)::date, count(*) FROM t_triggers")
    lo2, hi2, n2 = cur.fetchone()
    print("\n== 触发（t_triggers）窗口 %s → %s，%d 条" % (lo2, hi2, n2))
    cur.execute("SELECT status, count(*) FROM t_triggers GROUP BY 1 ORDER BY 2 DESC")
    print("   状态:", cur.fetchall())
    print("\n   腿型（尝试 / 执行）:")
    cur.execute("""SELECT event_type, direction, count(*),
                          sum(CASE WHEN status='executed' THEN 1 ELSE 0 END)
                   FROM t_triggers GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10""")
    for et, dr, c, ex in cur.fetchall():
        print("     %-26s %-5s %5s / %s" % (et, dr or "-", c, ex))
    print("\n   被拦 Top 6 原因:")
    cur.execute("""SELECT left(coalesce(reason,''),44) r, count(*) FROM t_triggers
                   WHERE status <> 'executed' GROUP BY 1 ORDER BY 2 DESC LIMIT 6""")
    for r, c in cur.fetchall():
        print("     %-46s %s" % (r, c))

    print("\n== T 账户真实往返价差（paper_trades account='t'，按时间邻接配对）==")
    cur.execute("""SELECT trade_date, symbol, direction, price FROM paper_trades
                   WHERE account_id='t' ORDER BY trade_date, id""")
    last, pairs = {}, []
    for d, sym, side, px in cur.fetchall():
        if side == "买入":
            last.setdefault(sym, []).append((d, float(px)))
        elif side == "卖出" and last.get(sym):
            d0, p0 = last[sym].pop(0)
            pairs.append((sym, d0, p0, d, float(px), (float(px) / p0 - 1) * 100))
    sp = sorted(p[5] for p in pairs)
    for p in pairs:
        print("   %-9s 买 %s @%-8.3f → 卖 %s @%-8.3f 价差 %+.2f%%" % p)
    if sp:
        mid = sp[len(sp) // 2]
        print("   n=%d 中位 %+.2f%% | ≥1%% 的 %d 笔 | <1%% 的 %d 笔"
              % (len(sp), mid, sum(1 for x in sp if x >= 1.0), sum(1 for x in sp if x < 1.0)))
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
